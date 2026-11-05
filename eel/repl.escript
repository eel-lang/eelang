#!/usr/bin/env escript
%%! -noshell
%% Persistent eel REPL: читает список абстрактных форм (как Erlang-терм),
%% вычисляет erl_eval:exprs с живыми bindings и отвечает строкой `OK Value` / `ERR Reason`.
%% По запросу `{'$pp', Form}` возвращает `PP <base64>` — erl_pp формы (для печати эквивалента).
%% Хелперы (_eel_pow, eel_iter, eel_print) предзаданы как fun-значения.
%% Вывод print копится в process dictionary и уходит в ответе строкой `OUT <base64>`.

main(_) ->
    B0 = erl_eval:new_bindings(),
    B1 = erl_eval:add_binding('_eel_pow', fun eel_pow/2, B0),
    B2 = erl_eval:add_binding('eel_iter', fun eel_iter/1, B1),
    B3 = erl_eval:add_binding('eel_mod', fun eel_mod/2, B2),
    B4 = erl_eval:add_binding('eel_floordiv', fun eel_floordiv/2, B3),
    B5 = erl_eval:add_binding('eel_print', fun eel_print/1, B4),
    B6 = erl_eval:add_binding('eel_join', fun eel_join/2, B5),
    B7 = erl_eval:add_binding('eel_zip', fun eel_zip/2, B6),
    B8 = erl_eval:add_binding('eel_enumerate', fun eel_enumerate/2, B7),
    B9 = erl_eval:add_binding('eel_range', fun eel_range/3, B8),
    loop(B9).

loop(Bindings) ->
    case io:get_line("") of
        eof ->
            halt(0);
        Line ->
            case parse_term(string:trim(Line)) of
                {ok, {'$pp', Form}} ->
                    Source = unicode:characters_to_binary(erl_pp:expr(Form)),
                    io:put_chars("PP " ++ binary_to_list(base64:encode(Source)) ++ "\n"),
                    loop(Bindings);
                {ok, Forms} when is_list(Forms) ->
                    eval(Forms, Bindings);
                {ok, Other} ->
                    io:put_chars("ERR " ++ io_lib:format("~p", [{bad_request, Other}]) ++ "\n"),
                    loop(Bindings);
                {error, Reason} ->
                    io:put_chars("ERR " ++ io_lib:format("~p", [Reason]) ++ "\n"),
                    loop(Bindings)
            end
    end.

eval(Forms, Bindings) ->
    put(eel_out, []),
    try erl_eval:exprs(Forms, Bindings) of
        {value, Value, NewBindings} ->
            flush_out(),
            io:put_chars("OK " ++ io_lib:format("~p", [Value]) ++ "\n"),
            loop(NewBindings);
        Other ->
            flush_out(),
            io:put_chars("ERR " ++ io_lib:format("~p", [Other]) ++ "\n"),
            loop(Bindings)
    catch
        Class:Reason ->
            flush_out(),
            io:put_chars("ERR " ++ io_lib:format("~p", [{Class, Reason}]) ++ "\n"),
            loop(Bindings)
    end.

%% Напечатанное клеткой — до строки ответа, отдельным фреймом (payload в base64).
flush_out() ->
    case get(eel_out) of
        undefined -> ok;
        [] -> ok;
        Out ->
            Text = unicode:characters_to_binary(lists:reverse(Out)),
            io:put_chars("OUT " ++ binary_to_list(base64:encode(Text)) ++ "\n")
    end,
    put(eel_out, []).

parse_term(Chars) ->
    case erl_scan:string(Chars) of
        {ok, Tokens, _} -> erl_parse:parse_term(Tokens);
        {error, E, _} -> {error, E}
    end.

%% int ** int (>= 0) — целочисленно, иначе math:pow (как в модульном бэкенде).
eel_pow(A, B) when is_integer(A), is_integer(B), B >= 0 -> eel_pow_loop(A, B, 1);
eel_pow(A, B) -> math:pow(A, B).

eel_pow_loop(_, 0, Acc) -> Acc;
eel_pow_loop(A, N, Acc) when N rem 2 =:= 0 -> eel_pow_loop(A * A, N div 2, Acc);
eel_pow_loop(A, N, Acc) -> eel_pow_loop(A, N - 1, Acc * A).

%% print: питоновское представление значения (строки — как есть, атомы — без кавычек).
eel_print(X) ->
    put(eel_out, [$\n, eel_str(X) | case get(eel_out) of undefined -> []; L -> L end]),
    nil.

eel_str(B) when is_binary(B) -> B;
eel_str(A) when is_atom(A) -> atom_to_binary(A, utf8);
eel_str(I) when is_integer(I) -> integer_to_binary(I);
eel_str(F) when is_float(F) -> float_to_binary(F, [short]);
eel_str(L) when is_list(L) -> [$[, lists:join(<<", ">>, [eel_str(X) || X <- L]), $]];
eel_str(T) when is_tuple(T) -> eel_str_tuple(tuple_to_list(T));
eel_str(M) when is_map(M) -> eel_str_map(lists:keysort(1, maps:to_list(M)));
eel_str(X) -> io_lib:format("~p", [X]).

eel_str_tuple([]) -> <<"()">>;
eel_str_tuple([One]) -> [$(, eel_str(One), <<",)">>];
eel_str_tuple(Items) -> [$(, lists:join(<<", ">>, [eel_str(X) || X <- Items]), $)].

%% zip: два списка, обрезка по короткому (как Python); enumerate: пары (N, X) с Start.
eel_zip(A, B) when is_list(A), is_list(B) -> lists:zip(A, B, trim);
eel_zip(A, _) when not is_list(A) -> erlang:error({bad_argument, zip, A});
eel_zip(_, B) -> erlang:error({bad_argument, zip, B}).

eel_enumerate(Start, List) when is_integer(Start), is_list(List) ->
    lists:enumerate(Start, List);
eel_enumerate(Start, _) when not is_integer(Start) ->
    erlang:error({bad_argument, enumerate, Start});
eel_enumerate(_, List) -> erlang:error({bad_argument, enumerate, List}).

%% range: питоновская семантика (стоп не включается), пустые диапазоны — [].
eel_range(Start, _, _) when not is_integer(Start) -> erlang:error({bad_argument, range, Start});
eel_range(_, Stop, _) when not is_integer(Stop) -> erlang:error({bad_argument, range, Stop});
eel_range(_, _, Step) when not is_integer(Step) -> erlang:error({bad_argument, range, Step});
eel_range(_, _, 0) -> erlang:error({bad_argument, range, 0});
eel_range(Start, Stop, Step) when Step > 0, Start >= Stop -> [];
eel_range(Start, Stop, Step) when Step < 0, Start =< Stop -> [];
eel_range(Start, Stop, Step) -> [Start | eel_range(Start + Step, Stop, Step)].

%% join: части — только строки, иначе {bad_argument, join, Часть} (как в модульном бэкенде).
eel_join(_Sep, []) -> [];
eel_join(_Sep, [H]) when is_binary(H) -> H;
eel_join(Sep, [H | T]) when is_binary(H) -> [H, Sep | eel_join(Sep, T)];
eel_join(_Sep, [H | _]) -> erlang:error({bad_argument, join, H}).

eel_str_map([]) -> <<"{}">>;
eel_str_map(Pairs) ->
    [${, lists:join(<<", ">>, [[eel_str(K), <<": ">>, eel_str(V)] || {K, V} <- Pairs]), $}].

%% map -> ключи, список -> как есть (иначе bad_generator).
eel_iter(M) when is_map(M) -> maps:keys(M);
eel_iter(L) -> L.

%% % и // — питоновская семантика для int и float.
eel_mod(A, B) when is_integer(A), is_integer(B) -> (A rem B + B) rem B;
eel_mod(A, B) -> math:fmod(math:fmod(A, B) + B, B).

eel_floordiv(A, B) when is_integer(A), is_integer(B) -> (A - ((A rem B + B) rem B)) div B;
eel_floordiv(A, B) -> float(math:floor(A / B)).
