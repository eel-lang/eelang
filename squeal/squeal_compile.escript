#!/usr/bin/env escript
%%! -noshell
%% Компиляция Erlang abstract forms squeal (см. specs/architecture.md).
%%
%% Сервер: читает из stdin по одному запросу-терму `{EtfPath, ErlFlag}.` и отвечает строкой
%%   OK                     — всё скомпилировано
%%   ERR <base64 текста>    — ошибка (формат `путь:строка: текст`, без дампа)
%% Формы приходят ETF-блобом: `[{Base, Forms, SourcePath}, ...]` (см. squeal/etf.py).
%% Процесс живёт до EOF — повторные сборки не поднимают VM заново.

main(_) ->
    loop().

loop() ->
    case io:get_line("") of
        eof ->
            halt(0);
        Line ->
            case parse_request(Line) of
                {ok, EtfPath, ErlFlag} ->
                    reply(compile_jobs(EtfPath, ErlFlag)),
                    loop();
                error ->
                    reply({error, "bad request"}),
                    loop()
            end
    end.

reply(ok) ->
    io:put_chars("OK\n");
reply({error, Text}) ->
    Encoded = base64:encode_to_string(unicode:characters_to_binary(Text)),
    io:put_chars("ERR " ++ Encoded ++ "\n").

parse_request(Line) ->
    case erl_scan:string(Line) of
        {ok, Tokens, _} ->
            case erl_parse:parse_term(Tokens) of
                {ok, {Path, ErlFlag}} when is_list(Path) -> {ok, Path, ErlFlag};
                _ -> error
            end;
        {error, _, _} ->
            error
    end.

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
    %% без report_*: компилятор не должен печатать в stdout — это канал протокола
    Options = [return, debug_info, {source, Source}],
    case compile:forms(Forms, Options) of
        {ok, _Mod, Binary} ->
            continue(Rest, ErlFlag, write(Base, Binary, Forms, ErlFlag));
        {ok, _Mod, Binary, _Warnings} ->
            continue(Rest, ErlFlag, write(Base, Binary, Forms, ErlFlag));
        {error, Errors, _Warnings} ->
            %% контракт ошибок: `путь:строка: текст`, без дампа терма
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
