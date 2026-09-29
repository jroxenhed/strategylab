# F435 design note: code nodes run real Python

- **Date:** 2026-09-29. Rewritten 2026-09-30 after John's decision.
- **Decision status: decided by John 2026-09-30.** Code nodes run real Python everywhere: on the Mac and on the public VM `strategylab01`. There is no sandbox.
- **Plan of record:** `docs/plans/2026-09-29-node-builder-finish-plan.md` (decision D1, divergence V4 withdrawn, Wave 7).
- **UI:** `docs/design/nodebuilder/surfaces-w5-w7.md`, surfaces S44 to S49.

---

## 1. Plain-English summary

- Code appears in three places, as the vision says: a formula on one parameter, a code block on any node, and a Wrangle node that is only code.
- The code is ordinary Python. numpy, pandas and a small StrategyLab helper module called `sl` are ready to use. Nothing is blocked. This is John's May vision: "Python, numpy/pandas/talib in scope via a curated sl module, no sandbox".
- Houdini shorthand works. `@close` reads a column. `@signal = ...` writes one.
- A call such as `chf("threshold", default=2.0)` turns into a real parameter on the node, as in Houdini.
- The same code runs the same way in a backtest and in a live bot.
- **The risk, accepted by John.** The server is on the public internet behind one Google sign-in, and it holds the broker keys. Anyone who steals that sign-in can run any program on the trading VM and read the keys. John chose real Python with this risk stated to him.
- **What we still add**, because it is cheap and blocks nothing: a switch that turns code off, a log line with a fingerprint of every piece of code that is saved or started, and guards so that broken or slow code pauses one bot instead of crashing the server.

---

## 2. The decision and the accepted risk

**What we asked (2026-09-29).** Two options. Option A was slx: a Python-looking formula language that we would interpret ourselves, which could not reach the machine. Option B was real Python, with its risk.

**The risk as stated to John.** "A stolen Google session could run any code on the trading VM and read the broker keys."

**The decision (John, 2026-09-30).** Option B, everywhere, with no sandbox. John accepted the risk above after it was stated to him. Option A is rejected (section 7).

**What the risk means in practice.** This table stays here so the risk is never forgotten.

| Fact | What real Python allows |
|---|---|
| `strategylab01` is on the public internet behind one Google login (oauth2-proxy) | One stolen session cookie, or one compromised browser, is enough to post a graph with code in it |
| The backend reads the IBKR and Alpaca keys from `/etc/strategylab/backend.env` | Code runs as the backend user and can read them |
| The backend runs live bots and talks to IB Gateway | Code can place, change or cancel orders outside every bot guard |
| The VM sits on the office network | Code can open connections to other office machines |
| Graphs travel: exported files, library assets, `bots.json` snapshots | Importing someone else's graph, or opening it with auto-cook on, runs its code. Treat a graph file like a program. |

**Not built, by decision:** no sandbox, no separate worker process, no allowlist, no restricted builtins. Section 4.12 lists what the design does not protect against.

---

## 3. What a code node computes

All three levels share one model: **read attributes, compute, write attributes.** All three go through the same runtime (section 4).

### 3.1 Level 1: parameter expression

- Any parameter whose catalog spec has `code_able: true` can switch to code with the `=` toggle. The stored value becomes `{"expr": "<source>"}`.
- The source is **one Python expression**, compiled in `eval` mode.
- It must return **one scalar** of the parameter's type:
  - int: a Python int, or a float with no fractional part;
  - float: any int or float;
  - bool: a bool;
  - string or select: a str (a select value must be one of its options, else `param_invalid`).
  - Anything else, including a Series, is `code_type`. For a Series the message adds "use a Wrangle for per-bar logic" (divergence V9).
- It can read constants, `ch*()` values, and **detail** attributes of the node's input stream (for example `@stop_pct`).
- Its `stream` holds detail attributes only. Reading a point (per-bar) attribute is `attr_missing` with the hint "parameter expressions read detail attributes only; use a Wrangle for per-bar logic". This also stops a parameter from reading the last bar of a backtest, which would leak the future into every bar.
- It is evaluated once per cook, when the evaluator reaches the node, after every parameter it reads through `ch()` (dependency order, plan item 7.B).
- Ticker `symbol`, `interval` and `prefix` are not code-able (`ticker_param_not_codeable`, divergence V5). This is unchanged: a bot trades one fixed symbol.
- Example: RSI `period` = `7 if chf("../vol/threshold") > 2 else 21`.

### 3.2 Level 2: node code block

- Every built-in node has an optional code block. Its source is `Node.code`, empty by default.
- It runs **after** the node's main computation, as a Python module body (`exec` mode).
- `stream` holds the node's input stream plus the node's own outputs under their write names. `ch("period")` reads the node's own params, built-in and spare.
- It may overwrite the node's own outputs and write new attributes. Writing a name that an upstream node wrote is `attr_clash`.
- Bypass skips the node and its code block together (pass-through).
- Example on an RSI node: `@rsi_smooth = sl.ema(@rsi, chi("smooth", default=3))`.

### 3.3 Level 3: Wrangle node

- Node type `wrangle`, category Code / Wrangle. Inputs `in0` to `in3`, merged by the normal stream rules. The code is the node.
- The body writes one or more attributes. A Series becomes a point attribute. A scalar becomes a detail attribute.
- Its parameters come from its `ch*()` calls (section 4.3).
- Example:

  ```python
  period = chi("atr_period", default=14, min=2, max=50)
  th = chf("vol_threshold", default=2.0)
  @atr_pct = sl.atr(@high, @low, @close, period) / @close * 100
  @vol_regime: bool = @atr_pct > th
  @rsi_adaptive = np.where(@vol_regime, sl.rsi(@close, 7), sl.rsi(@close, 21))
  ```

---

## 4. Runtime design

The runtime lives in the package `backend/nodebuilder/code/`.

| File | Job |
|---|---|
| `__init__.py` | Public API: `prepare(source, context, node) -> PreparedCode`, `run(prepared, ...)`, `code_enabled()`, `audit_log(...)` |
| `sugar.py` | The `@name` rewrite with `tokenize`, plus the column map back to the user's text |
| `promote.py` | The `ast` scan (parse only) for `ch*()` calls, attribute reads and writes, spare-param specs and reference paths |
| `runtime.py` | `compile`, the namespace, `exec`/`eval`, the `stream` proxy, result checks, exception-to-diagnostic mapping, the wall-clock limits and the leak counter |
| `sl.py` | The curated helper module |

Always import it as `nodebuilder.code`. Never put `backend/nodebuilder` itself on `sys.path`, because a top-level `code` package would hide Python's standard `code` module.

### 4.1 Pipeline

`prepare()` runs at `/validate`, `/parse_code`, compile and `bots.json` load. **It never runs user code.**

1. **Size.** The source is at most 8 KB of UTF-8. Otherwise `code_limit`. This keeps graph files and `bots.json` small. It is not a security limit.
2. **Sugar.** Rewrite `@name` (section 4.2). A bad sigil is `code_syntax` at the `@`.
3. **Parse.** `ast.parse(rewritten, mode="eval")` for a Level 1 expression, `mode="exec"` otherwise. A `SyntaxError` becomes `code_syntax`.
4. **Scan.** Walk the tree (section 4.3): spare params, `ch()` references, attribute reads and writes, write dtypes, the reserved name `stream`.
5. **Compile.** `compile(rewritten, filename=f"<code:{node_id}>", mode)`. The filename is unique per node, so a traceback frame can be matched to its node.
6. **Result.** `PreparedCode {node_id, context, source, sha256, code_obj, colmap, spare_params, refs, reads, writes, write_dtypes, lookback_bars}`. It is cached by `(sha256, context, node_id)`.

At cook time `run()` executes the code object (section 4.6). Nothing is parsed again per bar or per tick.

### 4.2 Houdini sugar: `@name`

`sugar.rewrite()` works on the `tokenize` token stream. Strings and comments are single tokens, so it never changes text inside them. For each `@` operator token:

1. **Find the previous significant token.** Skip `NL`, `COMMENT`, `INDENT` and `DEDENT` tokens.
2. **The `@` is in unary position** when that previous token is:
   - nothing (the start of the source), or `NEWLINE`. This is **line start**.
   - an operator or delimiter other than `)`, `]`, `}`, `...` and `.`. So `(`, `[`, `{`, `,`, `=`, `:`, `;`, `+`, `-`, `*`, `/`, `%`, `**`, `<`, `==`, `&`, `|`, `~`, `->`, `:=`, every augmented assignment, and `@` itself.
   - a keyword other than `True`, `False` and `None`. So `if`, `else`, `and`, `or`, `not`, `in`, `is`, `return`, `lambda`, `yield`, `await`, and so on. Soft keywords (`match`, `case`, `type`, `_`) count as plain names.
3. **Otherwise the `@` is binary matrix multiply** and is left alone. That is the case after a name, a number, a string, `True`/`False`/`None`, or a closing bracket. The `@=` operator is its own token and is never touched.
4. **A line-start `@` is a decorator** and is left alone when both of these hold:
   - its logical line has no assignment operator (`=`, `:=` or any augmented `op=`) outside brackets;
   - the next logical line starts with `def`, `class`, `async`, or another decorator.
5. **Every other unary `@`** must be followed, with no space, by a name that matches `^[a-z_][a-z0-9_]{0,63}$`. The pair becomes `stream["name"]`.
   - In a load position this is a read.
   - As an assignment target it is a subscript store, so a write goes through `stream.__setitem__`. This covers `@x = ...`, `@x += ...`, `@x: bool = ...` and `a, @b = ...`.
   - `@ close` (a space) or `@Close` (a capital) is `code_syntax` at the `@`.
6. **Positions.** The rewrite never adds or removes a line, so line numbers stay exact. A per-line offset table maps a column in the rewritten text back to the user's text. A column inside a rewritten span maps to its `@`.
7. **`stream` is a reserved name.** Binding it anywhere (assignment, `def stream`, `import x as stream`, a function parameter named `stream`) is `code_syntax` at the binding. The scan in section 4.3 checks this.

**Test vectors.** These were run against a prototype on Python 3.12 (the backend venv) on 2026-09-30. They go into `backend/tests/nodebuilder/vectors/sugar.json`.

| # | Source | Rewritten | Why |
|---|---|---|---|
| 1 | `@close` | `stream["close"]` | line start, no following `def` |
| 2 | `@x = @close * 2` | `stream["x"] = stream["close"] * 2` | line start write; read after `=` |
| 3 | `y = (@high + @low) / 2` | `y = (stream["high"] + stream["low"]) / 2` | after `(` and `+` |
| 4 | `f(@close, n=@period)` | `f(stream["close"], n=stream["period"])` | after `(` and `=` |
| 5 | `z = a @ b` | unchanged | after a name: matmul |
| 6 | `z = a@b` | unchanged | after a name: matmul |
| 7 | `z = f(x) @ w` | unchanged | after `)`: matmul |
| 8 | `z = m[0] @ w` | unchanged | after `]`: matmul |
| 9 | `z = x @ @close` | `z = x @ stream["close"]` | first `@` binary, second after `@` |
| 10 | `z = True @ w` | unchanged | `True` is a value |
| 11 | `@x += 1` | `stream["x"] += 1` | augmented write |
| 12 | `a, @b = 1, @c` | `a, stream["b"] = 1, stream["c"]` | after `,` |
| 13 | `v = -@close` | `v = -stream["close"]` | after unary `-` |
| 14 | `v = not @flag` | `v = not stream["flag"]` | after keyword `not` |
| 15 | `d = {"k": @close}` | `d = {"k": stream["close"]}` | after `:` |
| 16 | `if @close > 0: @flag = True` | `if stream["close"] > 0: stream["flag"] = True` | after `if` and `:` |
| 17 | `@x = 1; @y = 2` | `stream["x"] = 1; stream["y"] = 2` | after `;` |
| 18 | `x = (1 +` / `     @close)` (two lines) | `x = (1 +` / `     stream["close"])` | previous token `+` on the line above |
| 19 | `s = "@close"  # uses @close` | unchanged | string and comment |
| 20 | `t = f"{@close}"` | `t = f"{stream["close"]}"` | f-string field (needs Python 3.12 or later, which tokenizes f-strings) |
| 21 | `@x: bool = @a > @b` | `stream["x"]: bool = stream["a"] > stream["b"]` | annotated write |
| 22 | `@staticmethod` / `def f(): pass` | unchanged | decorator |
| 23 | `@functools.lru_cache(maxsize=None)` / `def g(x): return x` | unchanged | decorator (the `=` is inside brackets) |
| 24 | `@x = 1` / `def f(): pass` | `stream["x"] = 1` / `def f(): pass` | assignment at depth 0, so not a decorator |
| 25 | `def f():` / `    return @close` | `def f():` / `    return stream["close"]` | after `return` |
| 26 | `g = lambda: @close` | `g = lambda: stream["close"]` | after `:` |
| 27 | `x = @ close` | error `code_syntax` at 1:4 | space after the sigil |
| 28 | `x = @Close` | error `code_syntax` at 1:4 | name not lowercase |

The test also checks the column map both ways. In vector 2, rewritten column 22 (the `c` of `close` inside `stream["close"]`) maps back to column 5 (the `@`), and rewritten column 30 (the `*`) maps back to column 12. Both values were measured on the prototype.

### 4.3 Parameters (auto-promotion), references, reads and writes

`promote.scan(tree)` reads the tree only. It never evaluates it.

**`ch*` calls.**
- The scan finds every call to `ch`, `chf`, `chi`, `chs`, `chb` and `chv` by bare name.
- The first argument (or `name=`) must be a string literal. Otherwise `ch_dynamic` at the call: "ch() names must be literal strings, so parameters and their links are known before the code runs."
- The keywords `default`, `min`, `max`, `options` and `label` must be literals that `ast.literal_eval` accepts. Otherwise `ch_dynamic`.

**Bare names are this node's params.**
- A name without `/` belongs to the calling node.
- If the node has a built-in param with that name, the call reads it. Nothing is promoted.
- Otherwise the call declares a **spare param** `{name, type, default, min, max, options, label}`. The type comes from the function: `chf` float, `chi` int, `chs` string, `chb` bool, `chv` a vector of 2 to 4 floats.
- A bare `ch("x")` with no built-in param and no typed declaration is `ref_broken`.
- The first call with a name defines its spec. A later call with the same name but a different function or different keywords is `code_type` at the later call.
- The ordered list of specs becomes `Node.spare_params`. Values live in `Node.params[name]`. On first appearance a value is its default. `parse_code` returns the list (surface S48).

**Paths are references.** A name that contains `/` keeps the path rules of the plan (item 7.B):
- `ch("../other/param")` reads a sibling's param. `ch("../other/@attr")` reads a sibling's output attribute.
- `ch("../name")` reads a param of the enclosing network, for example a promoted param at a subnet root.
- Absolute paths such as `/shared/spread/@spread` work.
- Paths resolve against the pre-flatten hierarchy, so they work through flattened asset instances.
- `rename_node` rewrites these strings.
- References form the param dependency graph. A cycle is `ch_cycle` on every node in it. `/validate` exposes the edges as `param_deps` (plan, Wave 7 contracts).

**At run time** each `ch*` function answers only the names and paths the scan found. Any other call (for example through an alias, `f = chf; f(name)`) raises and becomes `ch_dynamic` with its line and column. So the static list is always complete.

**Reads and writes.**
- The scan collects `stream["literal"]` loads and stores, whether they came from the sugar or were typed by hand. Loads become `reads`. Stores become `writes`.
- A store with a key that is not a literal (`stream[name] = ...`) is `attr_dynamic`: "write attributes with a literal name (`@name = ...`) so later nodes can see them before the cook."
- A load with a non-literal key is allowed. A missing name then fails at run time with `attr_missing`.
- At run time the `stream` proxy refuses a write to any name that is not in `writes` (`attr_dynamic`).

**Write dtypes and classes before the cook.**
- An annotation fixes the dtype: `@vol_regime: bool = ...` or `@z: float = ...`.
- Without one, `/validate` reports the write with `dtype: "any"`.
- The scan cannot tell a per-bar write from a scalar write. So `/validate` reports every code write as a point attribute. At cook time a scalar is stored as a detail attribute (section 4.6).
- A consumer that needs a dtype or a class (an Entry needs a bool point attribute) gets no validate error for `any`. The cook checks the real value and reports `attr_type` on the writer.

**Lookback.**
- Every code-bearing node (a Wrangle, or a node with a code block) has the param `lookback_bars` (int, default 500, range 1 to 100,000).
- `parse_code` returns it first in `params`, unless the code declares it itself. So it shows as a normal spare row (S48).
- It enters `CompiledProgram.required_lookback_bars` the same way an indicator window does. The bot sizes its fetch window from it.
- It must be declared because the runtime cannot work out how far back arbitrary Python looks.
- Level 1 expressions have no lookback. They read scalars only.

### 4.4 The namespace

Each run gets a fresh globals dict with these names:

| Name | What it is |
|---|---|
| `np` | numpy |
| `pd` | pandas |
| `math` | the standard `math` module |
| `sl` | StrategyLab helpers (section 4.5) |
| `ta` | pandas_ta. `pandas-ta==0.4.71b0` is in `backend/requirements.txt` and imports in the venv. It is imported once, when `runtime.py` loads. |
| `stream` | the stream proxy (section 4.6) |
| `ch`, `chf`, `chi`, `chs`, `chb`, `chv` | bound to this node and this cook |
| `__builtins__` | the full `builtins` module. There is no sandbox, by decision. `import` works. |

- `talib` is **not** in scope. It is not in `backend/requirements.txt` and is not installed. Adding it later means adding it to `requirements.txt` and installing the TA-Lib C library on the Mac and on the VM. After that, `runtime.py` adds `talib` to the table when its import succeeds.
- A fresh globals dict per run means nothing carries over between cooks, or between bots, through code globals.

### 4.5 The `sl` module

**Rule: every `sl` helper calls the same backend function the matching node calls. It never re-implements a formula.** So `sl.rsi(@close, 14)` equals the RSI node's `@rsi` bar for bar, NaN warmup included.

| Helper | Returns | Same function as |
|---|---|---|
| `sl.sma(x, period)`, `sl.ema(x, period)` | Series | the `ma` node (`indicators.compute_ma`, types `sma` and `ema`) |
| `sl.rsi(x, period=14, type=<RSI node default>)` | Series | the RSI node (`indicators.compute_rsi` through `compute_instance`) |
| `sl.atr(high, low, close, period=14)` | Series | the ATR node (`indicators.compute_atr`) |
| `sl.macd(x, fast=12, slow=26, signal=9)` | named tuple `(line, signal, hist)` | the MACD node |
| `sl.bb(x, period=20, std=2.0)` | named tuple `(upper, middle, lower)` | the BB node |
| `sl.zscore(x, window)` | Series | `(x - mean) / std`, with the `rolling` node's mean and std |
| `sl.crosses_above(a, b)`, `sl.crosses_below(a, b)` | bool Series | the crosses nodes (`prev < r_prev and now >= r_now`; bar 0 is `False`). `b` may be a scalar. |
| `sl.rising(x, n=1)`, `sl.falling(x, n=1)` | bool Series | the slope nodes (`rising`, `rising_over`) |
| `sl.bars_since(cond)` | float Series | no node; NaN until `cond` is first true |
| `sl.shift(x, n=1)` | Series | the `shift` node. A negative `n` raises, because it would read the future. |
| `sl.rolling(x, window, op="mean")` | Series | the `rolling` node (`mean`, `min`, `max`, `std`, `sum`) |

- Defaults equal each node's catalog defaults. The RSI `type` default must match the RSI node's catalog default; it is not guessed.
- Inputs are Series on the cook's bar index, or scalars where noted. Outputs are Series on the same index.
- The indicator helpers go through the same adapter the indicator nodes use: the W2 code in `backend/nodebuilder/trading/nodes_indicators.py` that feeds a source column to `indicators.compute_instance`. If that adapter cannot be imported as it stands, item 7.A stops and asks the orchestrator. It never copies it.
- `code_capabilities.functions` lists every helper with its signature and a one-line doc. The editor uses it for completion (S47).

### 4.6 The `stream` proxy

**Reading.**
- `stream["close"]` returns a point attribute as a `pd.Series` on the cook's bar index, or a detail attribute as its value. Names have no sigil.
- A missing name raises, and becomes `attr_missing` with line and column.
- A Level 1 expression's proxy holds detail attributes only (section 3.1).
- pandas 3 copy-on-write is on. An in-place edit of a read column (`s = @close; s.iloc[0] = 0`) changes only the code's copy. The cook's columns stay intact.

**Writing.**
- A `pd.Series` must have exactly the cook's bar index. Otherwise `code_type`: "a written series must be aligned to the bar index".
- A 1-D numpy array of the bar count (for example from `np.where`) is wrapped on the index.
- A Python or numpy scalar (int, float, bool, str) becomes a detail attribute.
- A bool column stays bool and is never NaN. A float column stays float64, with NaN allowed. An int column becomes float64. Anything else (object, per-bar strings, datetimes) is `code_type`.
- Only names in the static `writes` list may be written (section 4.3).

### 4.7 Where code runs, and robustness

These rules keep the app up and correct. They are not security.

**Never on the event loop.**
- In the bot runner, the whole cook, code included, runs inside `self._run_in_executor` (plan D5; CLAUDE.md Key Bugs Fixed: never block the polling loop).
- In the API routes (`/backtest`, `/inspect`, `/preview`) the cook runs in a worker thread (a sync route or `run_in_threadpool`), never in an `async def` body. `/validate` and `/parse_code` never cook; they call `prepare()` only.

**One execution path.** `code.runtime.run()` is the only function that executes user code. The backtest, `/inspect`, `/preview` and the bot all reach it through the same kernel evaluator. So backtest/live parity holds by construction. A test checks it anyway (section 5).

**Wall-clock guard per cook.**
- The caller waits for the cook with a timeout: 10 s in a bot tick, 60 s in a backtest, `/inspect` or `/preview`. Both values are constants in `runtime.py` and appear in `code_capabilities.limits`.
- On timeout the cook fails with `code_timeout`. The diagnostic names the node that was running, because the runtime records the current node before each `run()`.
- **In a bot:** the tick places no order. The bot pauses with `pause_reason: "code_timeout: <node name> ran longer than 10 s"`, and the bot log says the same. It stays paused until John resumes it. Other bots keep ticking.
- **In a route:** the response is a 400 with the `code_timeout` diagnostic.
- **A runaway thread cannot be killed in-process.** Python has no safe way to stop a thread from outside. The cook keeps running, and keeps its thread in the default executor pool, until it ends by itself or the backend restarts. So it leaks until the process restarts.
  - Its late result is thrown away. Each result carries its tick id, and the runner drops a result whose tick is over.
  - The runtime counts leaked cooks. `code_capabilities` returns `leaked_cooks`, and each leak is logged at WARNING.
  - Several leaks can use up the executor pool. The fix is a backend restart.
  - We do not use `PyThreadState_SetAsyncExc`. It cannot interrupt numpy or pandas C code, and it can fire inside our own cleanup code.

**Exceptions become diagnostics.** `run()` catches every exception from user code and turns it into a diagnostic with `node_id`, `code`, `message` (the exception type and text), `line` and `col`.
- **`SyntaxError`** (from `prepare()`): `line = e.lineno` and `col = e.offset - 1`, because Python's `offset` is 1-based. Then the column goes through the sugar column map.
- **Run-time errors:** walk the traceback. Take the **last** frame whose `f_code.co_filename` is this node's `<code:{node_id}>` filename. So an error deep inside numpy points at the user's line that called numpy.
  - `traceback.extract_tb` gives `lineno`, `colno`, `end_lineno` and `end_colno` (Python 3.11 and later).
  - `colno` is a UTF-8 byte offset. The runtime converts it to a character offset in the rewritten line, then maps it back through the sugar column map.
- **Codes:** `code_runtime` for an ordinary exception. `attr_missing`, `code_type`, `ch_dynamic` or `attr_dynamic` when the exception is one the runtime raised on purpose.
- **In a bot:** a code exception fails that tick's cook. No order is placed. The error is logged with its diagnostic, and the bot pauses with `pause_reason: "code_runtime: <node name> line 3: ZeroDivisionError: division by zero"`. A bot never trades on a partial cook.

**Lookback.** See section 4.3. A code node that reads further back than its `lookback_bars` gives a different last value live than in the backtest. The W2 live-window parity test gains a code fixture.

**Look-ahead is the author's job.** `sl.shift` refuses a negative shift, but raw pandas can read the future (`@close.shift(-1)`). The backtest cannot stop this. The live parity check catches it: a Wrangle that reads the future shows a different last value live than in the backtest.

**Crashes are not caught.** Code can still end the process (`os._exit`), use up all memory, or hang inside C code. By decision there is no process isolation. The backend then needs a restart, as after any crash. Bots come back from `bots.json`.

### 4.8 Diagnostics

Every code problem is a normal diagnostic (plan section 4.2) with `node_id`, `code`, `message`, `line`, `col`, `end_line` and `end_col`.

**Position convention (orchestrator decision, 2026-09-30).**
- `line` is 1-based. `col` is 0-based. Both count characters in the user's original text, as Python's `ast` reports them.
- `end_line` and `end_col` follow the same rule. They may be null.
- The frontend converts to Monaco's 1-based column in one function, `toMonacoRange` (surface S47).
- The `parse_code` docstring states this convention.

**Codes added in W7.**

| Code | Severity | When |
|---|---|---|
| `code_syntax` | error | invalid Python, a bad `@` sigil, or binding the reserved name `stream` |
| `code_limit` | error | a source larger than 8 KB |
| `code_runtime` | error | an exception while the code ran |
| `code_timeout` | error | the wall-clock guard fired |
| `code_type` | error | a wrong result: a Series in a scalar param, a scalar of the wrong type, a misaligned or unsupported written value, or two conflicting `ch*` specs |
| `ch_dynamic` | error | a `ch*` name, path or keyword that is not a literal, or a `ch*` call the scan did not see |
| `attr_dynamic` | error | an attribute write whose name is not a literal |
| `ch_cycle` | error | parameters that read each other in a loop |
| `code_disabled` | error | a code-bearing graph while `SL_CODE_NODES=0` |
| `ticker_param_not_codeable` | error | an expression on a Ticker `symbol`, `interval` or `prefix` |

- Code also uses `attr_missing`, `attr_type` and `attr_clash` (W2) and `ref_broken` (W6).
- `code_forbidden` no longer exists. It was the slx allowlist error.
- `POST /api/nodebuilder/parse_code` returns these while the user types. It runs `prepare()` only and never runs code.

### 4.9 Kill switch

- The environment variable `SL_CODE_NODES` lives in `/etc/strategylab/backend.env` on the VM and in `backend/.env` on the Mac. The default is `1` (on) everywhere. A change needs a backend restart.
- With `SL_CODE_NODES=0`:
  - Compile rejects any graph with code (`code_disabled`). That covers `/validate`, `/backtest`, `/inspect`, `/preview`, spawn and `graph_update`.
  - Saving a graph still works. It is data, and nothing runs.
  - A code-bearing bot pauses on its next tick with `pause_reason: "code_disabled"`. It never crash-loops. Starting it is refused with a clear `detail`.
  - `GET /api/nodebuilder/code_capabilities` returns `enabled: false`, and the UI shows surface S49.
- The switch is for incidents ("turn code off now"). It is not a security boundary.

### 4.10 Audit trail

This is cheap and it blocks nothing.

- **On every save of a code-bearing graph** (`POST` and `PUT /api/graphs`, the seed import, and a library asset save), log one line per code snippet at INFO on the logger `strategylab.code_audit`:

  ```text
  code_audit event=graph_save graph_id=g_3f9a1c7e2b40 rev=5 node_id=n_spread name=spread_z level=wrangle sha256=<64 hex> bytes=412 email=<X-Forwarded-Email or ->
  ```

  `level` is `expr:<param>`, `node_code` or `wrangle`.
- **On every bot start** (and on spawn and `graph_update`), log the same lines with `event=bot_start bot_id=...` in the backend log, plus one summary line in the bot's own log.
- The hash covers the user's original source as UTF-8, before the sugar rewrite. So the same text gives the same hash on the Mac and on the VM.
- Nothing is ever refused because of this log. It answers one question after the fact: which code ran, and when did it first appear?

### 4.11 Loading `bots.json`

- On load, `BotManager` runs `prepare()` (syntax only, no execution) on every code snippet in each graph bot's snapshot.
- A failure pauses that bot with `pause_reason: "code_syntax: <node name> line 3"`. The manager and the other bots load normally. The manager never crashes.
- With `SL_CODE_NODES=0`, a code-bearing bot loads paused with `code_disabled`.
- Loading writes no audit lines. Starting does.

### 4.12 What this design does not protect against

By decision:
- A stolen login, or an imported graph, can run any program on the machine, read the broker keys, place orders outside the bot guards, and reach the office network.
- Code can crash or hang the backend.
- Code can read the future in a backtest.
- A runaway thread lives on until the backend restarts.

---

## 5. Tests

All of these land in Wave 7. The plan (W7) owns the file list.

| Test | File | What it checks |
|---|---|---|
| Sugar rewrite | `backend/tests/nodebuilder/code_nodes/test_sugar.py` with `backend/tests/nodebuilder/vectors/sugar.json` | every vector in section 4.2, the column map in both directions, and both error cases |
| Auto-promotion | `backend/tests/nodebuilder/code_nodes/test_promote.py` | `chf`/`chi`/`chs`/`chb`/`chv` with literals give the right specs in order; a built-in name is read, not promoted; `lookback_bars` comes first; a non-literal name or keyword is `ch_dynamic`; conflicting specs are `code_type`; reads, writes and annotation dtypes; binding `stream` is `code_syntax`; a non-literal write is `attr_dynamic` |
| `ch()` scoping | `backend/tests/nodebuilder/test_ch_refs.py` with `vectors/ch_refs.json` (shared with vitest) | bare name; `../sibling/param`; `../sibling/@attr`; `../name` from a subnet child; absolute paths; pre-flatten resolution; `rename_node` rewrites; `ch_cycle`; a run-time alias call is `ch_dynamic` |
| Exception to diagnostic | `backend/tests/nodebuilder/code_nodes/test_runtime.py` | `x = 1 / 0` on line 3 gives `code_runtime` at line 3, col 4; an error inside numpy or pandas points at the user's calling line; a `SyntaxError` maps to `offset - 1`; a column after a rewritten `@attr` maps back to the user's text; an error inside a user `def` points inside the def |
| Timeout to failed cook to bot pause | `backend/tests/test_code_timeout.py` | a Wrangle running `import time; time.sleep(2)` with the bot guard set to 0.2 s: the tick places no order (mocked broker); the bot pauses with a `pause_reason` that starts with `code_timeout`; a second bot's tick completes on time; `leaked_cooks` rises by 1; the late result is dropped. The sleep ends by itself, so the suite leaks nothing. |
| Backtest/live parity | `backend/tests/nodebuilder/test_code_live_parity.py` | a graph with one code param (an RSI `period` expression) and one Wrangle: the bot path over the live window gives the same signals as the backtest on the last N bars |
| Kill switch | `backend/tests/test_code_kill_switch.py` | with `SL_CODE_NODES=0`, validate, backtest, inspect, preview, spawn and `graph_update` refuse with `code_disabled`; save works; an existing bot pauses with `pause_reason: "code_disabled"` and does not crash-loop; `code_capabilities.enabled` is false |
| `bots.json` syntax error on load | `backend/tests/test_code_botsjson_load.py` | a snapshot whose code has a syntax error: that bot loads paused with `code_syntax`; the other bots load normally; the manager does not raise |
| `sl` numeric parity | `backend/tests/nodebuilder/code_nodes/test_sl_parity.py` | each helper equals its node's output exactly (`pd.testing.assert_series_equal(check_exact=True)`), including the NaN warmup positions, on a real cached frame and on a synthetic frame with gaps; `sl.shift(x, -1)` raises |
| Levels | `backend/tests/nodebuilder/test_code_levels.py` | an expression returns a scalar of the param's type; a point read in an expression is `attr_missing`; a Series result is `code_type`; a Ticker expression is `ticker_param_not_codeable`; a code block runs after its node; a Wrangle merges `in0` to `in3` |
| Audit | `backend/tests/test_code_audit.py` | a graph save and a bot start each log one `code_audit` line per snippet, with the sha256 of the original source |

---

## 6. Where it lands

Wave 7 in the plan owns the order, the file ownership and the contracts:
- **7.A** builds `backend/nodebuilder/code/` (sections 4.1 to 4.6, the limits, the switch and the audit helper).
- **7.B** builds `ch()` references and the param dependency graph (section 4.3), including `param_deps` for `/validate`.
- **7.C** wires the three levels into compile, the routes, the bot runner and the bot manager (sections 4.7 to 4.11).
- **7.D** builds the editor: Monaco's Python language with the `@attr` sugar (surfaces S44 to S49).

---

## 7. Rejected: slx (option A)

- **What it was.** A Python-looking formula language. We would parse it with `ast`, allow only a short list of shapes (math, comparisons, calls to our own functions), and interpret it ourselves. It would never reach `exec`.
- **Why it was offered.** The server had become public, and it holds the broker keys. slx made a stolen login no more powerful than the order API, and it needed no sandbox. It was the recommendation of 2026-09-29.
- **Why it was rejected.** John wants the vision as written: real Python with numpy, pandas and TA libraries, and no sandbox. He accepted the risk in section 2 on 2026-09-30. slx could not import libraries or loop over bars, so every missing capability would have needed a new built-in node.
- **Also rejected earlier:** a sandboxed Python worker process (1 to 2 weeks of extra work, and a sandbox that must stay exactly right forever), and real Python on the Mac only (two languages, and one wrong flag on the VM would expose the keys anyway).
