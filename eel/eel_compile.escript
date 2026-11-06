#!/usr/bin/env escript
%%! -noshell
%% Compiling eel Erlang abstract forms (see specs/architecture.md).
%%
%% Two modes:
%%   escript eel_compile.escript
%%       stdin loop: a request per line, a reply per line (an escript inside a Python process)
%%   escript eel_compile.escript --daemon <SocketPath> <PidFile>
%%       daemon: listens on a unix socket and runs until {stop} arrives
%%
%% Requests (Erlang terms, one per line):
%%   {compile, EtfPath, ErlFlag}.  → OK | ERR <base64 of the text>
%%   {ping}.                       → OK <base64 "version=… compiled=…">
%%   {stop}.                       → OK, remove the socket and exit (daemon mode only)
%% The forms are an ETF blob `[{Base, Forms, SourcePath}, …]` (see eel/etf.py).

-define(PROTOCOL, "2").

main([]) ->
    loop(0);
main(["--daemon", SocketPath, PidFile]) ->
    daemon(SocketPath, PidFile);
main(Args) ->
    io:format(standard_error, "usage: eel_compile.escript [--daemon SOCKET PIDFILE]~n", []),
    io:format(standard_error, "unexpected arguments: ~p~n", [Args]),
    halt(2).

%% --- stdin mode ---

loop(Compiled) ->
    case io:get_line("") of
        eof ->
            halt(0);
        Line ->
            case handle(Line, Compiled) of
                {reply, Text, Next} ->
                    io:put_chars(Text),
                    loop(Next);
                {reply_and_stop, Text, _Next} ->
                    io:put_chars(Text),
                    halt(0)
            end
    end.

%% --- daemon mode ---

daemon(SocketPath, PidFile) ->
    Options = [
        {ifaddr, {local, SocketPath}},
        binary,
        {packet, line},
        {active, false},
        {reuseaddr, true},
        {backlog, 16}
    ],
    case gen_tcp:listen(0, Options) of
        {ok, Listen} ->
            filelib:ensure_dir(PidFile),
            ok = file:write_file(PidFile, os:getpid()),
            %% the parent (the build server) holds our stdin as a pipe: closed means it died
            _Watchdog = spawn(fun watch_parent/0),
            accept(Listen, SocketPath, PidFile, 0);
        {error, Reason} ->
            io:format(standard_error, "cannot listen on ~ts: ~p~n", [SocketPath, Reason]),
            file:delete(PidFile),
            halt(2)
    end.

%% the daemon lives only with its parent: EOF on stdin means the parent is gone
watch_parent() ->
    case io:get_line("") of
        eof -> halt(0);
        _ -> watch_parent()
    end.

accept(Listen, SocketPath, PidFile, Compiled) ->
    case gen_tcp:accept(Listen) of
        {ok, Socket} ->
            case serve(Socket, Compiled) of
                {stop, _} ->
                    gen_tcp:close(Listen),
                    file:delete(SocketPath),
                    file:delete(PidFile),
                    halt(0);
                Next ->
                    accept(Listen, SocketPath, PidFile, Next)
            end;
        {error, _} ->
            accept(Listen, SocketPath, PidFile, Compiled)
    end.

serve(Socket, Compiled) ->
    case gen_tcp:recv(Socket, 0, 60000) of
        {ok, Line} ->
            case handle(Line, Compiled) of
                {reply, Text, Next} ->
                    gen_tcp:send(Socket, Text),
                    serve(Socket, Next);
                {reply_and_stop, Text, Next} ->
                    gen_tcp:send(Socket, Text),
                    gen_tcp:close(Socket),
                    {stop, Next}
            end;
        {error, _} ->
            gen_tcp:close(Socket),
            Compiled
    end.

%% --- requests ---

handle(Line, Compiled) ->
    case parse_request(Line) of
        {compile, Path, ErlFlag} ->
            case compile_jobs(Path, ErlFlag) of
                ok -> {reply, "OK\n", Compiled + 1};
                {error, Text} -> {reply, error_line(Text), Compiled}
            end;
        ping ->
            {reply, ok_line(io_lib:format("version=~ts compiled=~p", [?PROTOCOL, Compiled])), Compiled};
        stop ->
            {reply_and_stop, "OK\n", Compiled};
        error ->
            {reply, error_line("bad request"), Compiled}
    end.

parse_request(Line) ->
    case erl_scan:string(chars(Line)) of
        {ok, Tokens, _} ->
            case erl_parse:parse_term(Tokens) of
                {ok, {compile, Path, ErlFlag}} when is_list(Path) -> {compile, Path, ErlFlag};
                {ok, {ping}} -> ping;
                {ok, {stop}} -> stop;
                _ -> error
            end;
        {error, _, _} ->
            error
    end.

%% stdin mode yields a string, the socket a binary
chars(Line) when is_binary(Line) -> binary_to_list(Line);
chars(Line) -> Line.

ok_line(Text) ->
    "OK " ++ base64:encode_to_string(unicode:characters_to_binary(Text)) ++ "\n".

error_line(Text) ->
    "ERR " ++ base64:encode_to_string(unicode:characters_to_binary(Text)) ++ "\n".

%% --- compilation ---

compile_jobs(EtfPath, ErlFlag) ->
    case file:read_file(EtfPath) of
        {ok, Blob} ->
            try binary_to_term(Blob) of
                Jobs when is_list(Jobs) -> compile_all(Jobs, ErlFlag)
            catch
                _:_ -> {error, "cannot decode forms"}
            end;
        {error, Reason} ->
            {error, io_lib:format("cannot read forms: ~p", [Reason])}
    end.

compile_all(Jobs, ErlFlag) ->
    compile_each(Jobs, ErlFlag).

compile_each([], _ErlFlag) ->
    ok;
compile_each([{Base, Forms, Source} | Rest], ErlFlag) ->
    %% no report_*: stdout/the socket is the protocol channel, errors come back as data
    Options = [return, debug_info, {source, Source}],
    case compile:forms(Forms, Options) of
        {ok, _Mod, Binary} ->
            continue(Rest, ErlFlag, write(Base, Binary, Forms, ErlFlag));
        {ok, _Mod, Binary, _Warnings} ->
            continue(Rest, ErlFlag, write(Base, Binary, Forms, ErlFlag));
        {error, Errors, _Warnings} ->
            %% the error contract: `path:line: text`, never a term dump
            {error, format_errors(Source, Errors)};
        _Other ->
            {error, io_lib:format("~ts: compilation failed", [Source])}
    end.

continue(Rest, ErlFlag, ok) ->
    compile_each(Rest, ErlFlag);
continue(_Rest, _ErlFlag, {error, Text}) ->
    {error, Text}.

write(Base, Binary, Forms, ErlFlag) ->
    case file:write_file(Base ++ ".beam", Binary) of
        ok when ErlFlag ->
            case file:write_file(Base ++ ".erl", [erl_pp:form(Form) || Form <- Forms]) of
                ok -> ok;
                {error, Reason} -> {error, io_lib:format("cannot write ~ts.erl: ~p", [Base, Reason])}
            end;
        ok ->
            ok;
        {error, Reason} ->
            {error, io_lib:format("cannot write ~ts.beam: ~p", [Base, Reason])}
    end.

format_errors(Source, [{_File, Problems} | _]) ->
    [format_problem(Source, P) || P <- Problems];
format_errors(Source, _) ->
    [io_lib:format("~ts: compilation failed~n", [Source])].

format_problem(Source, {Location, Module, Description}) ->
    io_lib:format("~ts:~p: ~ts~n", [Source, line_of(Location), describe(Module, Description)]).

line_of({Line, _Column}) -> Line;
line_of(Line) when is_integer(Line) -> Line;
line_of(_) -> 1.

describe(erl_lint, {undefined_function, {Name, Arity}}) ->
    io_lib:format("function ~ts/~p is not defined", [Name, Arity]);
describe(erl_lint, {undefined_function, {Name, Arity}, Defined}) ->
    io_lib:format("function ~ts/~p is not defined (defined: ~p)", [Name, Arity, Defined]);
describe(erl_lint, {unused_var, Name}) ->
    io_lib:format("variable ~ts is unused", [Name]);
describe(erl_lint, {redefine_type, _}) ->
    "duplicate type definition";
describe(compile, {crash, Module, Kind, _Stack}) ->
    io_lib:format("internal Erlang compiler error (~p:~p) — check the generated forms", [Module, Kind]);
describe(Module, Description) ->
    io_lib:format("~p: ~p", [Module, Description]).
