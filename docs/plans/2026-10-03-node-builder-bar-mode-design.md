# F435 design note: bar mode, VEX-style code for one bar

- **Date:** 2026-10-03.
- **Status: proposal for John to approve.** Nothing here is built.
- **Builds on:** `docs/plans/2026-09-29-node-builder-code-nodes-design.md` (the code-nodes note, "W7 note" below) and Wave 7 of `docs/plans/2026-09-29-node-builder-finish-plan.md`. It changes no Wave 7 contract. Every change is an addition with a default that keeps Wave 7 behaviour.
- **Prototype and benchmarks:** throwaway files in the session scratch folder (section 5.6). They are not in the repo.

---

## 1. Plain-English summary

- Wave 7 code is **column code**. `@close` is the whole close column, and you write numpy (`np.where(...)`).
- **Bar mode** is a switch on a Wrangle node or a node code block. With it on, you write the code for **one bar**, as in Houdini VEX. The loop over bars is implied:

  ```python
  if @close > @open and @rsi < 30:
      @signal = True
  ```

- It is still Python. We do not build a new language (slx stays rejected; W7 note section 7).
- **Speed.** When the code is saved, StrategyLab picks the fastest of three ways to run it, called **tiers**:
  - **vectorized:** the one-bar code is rewritten into column code. This is as fast as hand-written numpy.
  - **compiled:** the loop is compiled to machine code with numba. Numba is a compiler for numeric Python. It is already installed with pandas-ta. This tier is for code that keeps a value from one bar to the next.
  - **python loop:** plain Python, one bar at a time. This is the fallback. A small badge on the node shows the tier, so you can see when your code is slow.
- **Measured on this Mac** (500,000 bars): vectorized and compiled code takes 0.8 to 3 ms. The Python loop takes 60 to 180 ms, which is 20 to 130 times slower. (When `sl` helpers dominate, every tier takes about 14 ms, because the helpers themselves cost that.) Compiling costs about 40 to 130 ms once per snippet, plus about 0.2 s once per backend start. A disk cache cuts the per-snippet cost to about 1.5 ms.
- Bar mode gives **exactly** the same numbers as the same logic in column mode, in every tier. Tests check this on random data.

---

## 2. Where bar mode lives

### 2.1 Which code gets the switch

| Code level | Bar mode? | Why |
|---|---|---|
| Parameter expression (Level 1) | **No** | It returns one scalar, once per cook. There is no bar to loop over. A Series result stays `code_type`. |
| Node code block (Level 2) | **Yes** | It runs per bar over the node's output stream. Example: clamp an RSI with `if @rsi > 90: @rsi = 90`. |
| Wrangle node (Level 3) | **Yes** | This is the main use. |

### 2.2 How a node declares it

- A new model field, `Node.code_mode: "column" | "bar"`, default `"column"`. Old graphs load as column mode. It applies only when `Node.code` is not empty.
- **Why a field and not a param.** A param can hold an expression, be read by `ch()`, or be promoted. The mode must be fixed when the code is prepared, because the tier depends on it. A built-in node also has no catalog param for it.
- **Why not a comment such as `# mode: bar`.** It is hidden, easy to delete by mistake, and the toggle would have to edit the user's text.

### 2.3 The UI toggle

- The code drawer header (S45, and the Wrangle body) gets a two-way switch: **Run over: Columns | Each bar.** Houdini calls the same choice "Run Over" on its Attribute Wrangle.
- Next to it is the tier badge: **vectorized** (green), **compiled** (blue) or **python loop** (amber). The tooltip says why, for example "compiled: line 1 keeps state across bars (`state.peak`)".
- For a vectorized snippet, a "Show column code" link opens the generated numpy code, read only. Switching the mode keeps the text; the code is checked again in the new mode, and errors show at once.

---

## 3. Language rules for one bar

### 3.1 Reading the current bar

- `@close` is **one number**: the close of the current bar (a Python `float`). A bool column gives one `bool`. A detail attribute (for example `@stop_pct`) is the same scalar on every bar.
- Reading an attribute the snippet itself wrote earlier in the same bar gives the new value. Before the write, it gives the start value (section 3.3). This is VEX's rule.

### 3.2 History: `@close[-n]`

- `@close[-1]` is the previous bar. `@close[-n]` is n bars back. `@close[0]` is the current bar.
- Before the first bar there is no value. A float reads NaN (not a number) and a bool reads `False`. This matches the `shift` node's NaN warmup.
- The index may be an int literal, a name bound to a constant `chi()` (`n = chi("n", default=3)`, then `@close[-n]`), or, in the compiled and Python tiers, any int computed on the bar, such as a loop counter.
- **A positive index reads the future** (lookahead). A literal one, such as `@close[1]`, is refused when the code is prepared: `bar_lookahead` at the index. A computed index that turns out positive fails that bar with the same code.
- `@x[-1]` on an attribute the snippet writes is the **final value of `@x` on the previous bar**. This gives recursive formulas (example E6).
- Writing to the past (`@x[-1] = ...`) is `code_syntax`. The sugar is unchanged. `@close[-1]` becomes `stream["close"][-1]`, which is valid Python. Bar mode reads that shape from the parsed tree.

### 3.3 Writes and start values

- `@x = value` sets `@x` on the current bar. Every bar-mode write is a **point** (per-bar) attribute. There are no detail writes in bar mode. This also answers the W7 question "point or detail?" before the cook.
- **Start value on each bar (VEX rule).** In a code block, the node's own output (for example `@rsi`) starts at the value the node computed. A new attribute starts at `0.0`, or `False` for a bool. To start at NaN, write `@x = nan` as the first line.
- W7's write rules still hold: only literal names, `attr_clash` for upstream names, a bool column never holds NaN.

### 3.4 Types

- VEX types names with a prefix (`f@x`, `i@x`). Python cannot do that: `f@x` is already matrix multiply. We reuse Wave 7's **annotation rule** instead: `@sig: bool = ...` and `@z: float = ...` fix an attribute's type. Locals may be annotated `int`, `float` or `bool` (`count: int = 0`); this is optional.
- Without an annotation, an attribute is **bool** when every value written to it is a comparison, `True`/`False` or another bool. Otherwise it is **float**. So `@signal = 1` is a float. Use `True` to feed an Entry node.
- Writing a value that is not bool to a bool attribute is `code_type` when the code is prepared, if it can be seen then. Otherwise it fails at the first cook, before any bar runs.
- Bar mode values are numbers and bools only. Strings and lists are allowed only as locals, and only in the Python loop tier.

### 3.5 State that carries across bars

VEX has no per-point state. Traders need it, for example a running high since a signal. Bar mode adds **`state`**:

```python
state.peak: float = nan          # declaration: runs once, before the first bar
state.peak = max(state.peak, @high)   # update: the value carries to the next bar
```

- A **declaration** is a top-level annotated assignment `state.name: type = <constant>`. The type (`float`, `int` or `bool`) and the start value are required. The value must be a literal, `nan` or a `ch*()` call. This is Pine Script's `var` in Python clothes.
- An **update** is any plain `state.name = ...`. Using an undeclared `state.name` is `bar_unsupported` at that spot.
- Plain locals reset on every bar. A local that might be unset on some path is `bar_unsupported` ("give `x` a value before the `if`").
- `@x[-1]` on the snippet's own writes is the other way to carry state (section 3.2).
- **Not the trading position.** `state` is the snippet's own memory. It cannot see real fills, stops or the simulated position. Those stay with the W8 position-state nodes (plan section 10, divergence V6).

### 3.6 `ch()`

- `ch*()` works exactly as in Wave 7: the same literal rules, auto-promotion and reference paths. Its value is **constant over the run**. It is read once before the first bar and passed into the loop as an argument. It is never baked into the compiled code, so an optimizer sweep over `chf("trail_pct")` compiles once, not once per value.

### 3.7 `sl` helpers per bar

- An `sl.*` or `ta.*` (pandas_ta) call means "this helper's value **at this bar**". For example, `sl.rsi(@close, 14) < 30`.
- It is **hoisted**: computed once as a whole column before the loop, then read per bar. So `sl.rsi` in bar mode equals the RSI node bar for bar, warmup included (W7's `sl` parity rule).
- Its arguments must be **bar-invariant**: whole attributes (`@close`), constants, `ch*()` values, or other hoisted calls. A per-bar local as an argument is `bar_unsupported`: "sl.rsi needs a column; pass @close, not a per-bar value".
- Hoisted calls that make sense per bar: `sl.sma`, `ema`, `rsi`, `atr`, `macd(...).hist`, `bb(...).upper`, `zscore`, `rolling`, `crosses_above`, `crosses_below`, `rising`, `falling` and `bars_since(@cond)`. `sl.shift` works, but `@x[-n]` is the bar-mode way.

### 3.8 Math on one bar

- `abs`, `min`, `max`, `round`, `nan`, `isnan(x)` and the `math` functions (`sqrt`, `log`, `exp`, `floor`, `ceil` and so on) are in scope.
- They follow **numpy's rules** in every tier (section 6.2). `np.sqrt(x)` on one bar is the same as `math.sqrt(x)`.

### 3.9 Setup lines and what is not allowed

- **Setup lines** run once, before the first bar: top-level `import`, top-level `def`, and `state` declarations.
- **Refused** when the code is prepared (`bar_unsupported`, at the construct): `global`, `nonlocal`, `yield`, `await`, `class` and `del @x`; methods on a per-bar value (`@close.rolling(5)`: "in bar mode @close is one number; use sl.rolling or column mode"); `stream[...]` with a key that is not a literal, as in Wave 7; and `print`, which would print once per bar (the editor suggests a debug attribute).
- **Allowed but slow:** any other Python, such as dicts, strings, `try`, or calls to your own functions. It runs in the Python loop tier, and the badge says so. This keeps the Wave 7 decision: real Python, nothing blocked beyond what breaks the one-bar model.
- **Early exit.** `return` ends the current bar, as in VEX. `break` and `continue` work inside your own loops.

---

## 4. Examples: column mode and bar mode side by side

Every pair gives identical output. The prototype checked E1 (with a float signal), E2, E4 and E5 on 5,000 and 500,000 bars (section 5.6).

**E1. A pure condition** (tier: vectorized)

```python
# column mode
@signal = (@close > @open) & (@rsi < 30)

# bar mode
if @close > @open and @rsi < 30:
    @signal = True
```

**E2. Three-way trend with history** (tier: vectorized, as `np.select`)

```python
# column mode
p1, p2 = sl.shift(@close, 1), sl.shift(@close, 2)
up = (@close > p1) & (p1 > p2)
down = (@close < p1) & (p1 < p2)
@trend = np.select([up, down], [1.0, -1.0], 0.0)

# bar mode
if @close > @close[-1] and @close[-1] > @close[-2]:
    @trend = 1
elif @close < @close[-1] and @close[-1] < @close[-2]:
    @trend = -1
```

**E3. Breakout with a param** (tier: vectorized)

```python
# column mode
n = chi("n", default=20)
@breakout = @close > sl.rolling(sl.shift(@high, 1), n, op="max")

# bar mode
@breakout = @close > sl.rolling(sl.shift(@high, 1), chi("n", default=20), op="max")
```

The rolling max is hoisted. Bar mode adds nothing here: not every snippet gets better in bar mode.

**E4. Trailing exit since an entry signal** (tier: compiled)

```python
# column mode: no vector form exists, so you write a Python loop over arrays
entry, high, close = @entry.to_numpy(), @high.to_numpy(), @close.to_numpy()
pct = chf("trail_pct", default=5.0)
ex = np.zeros(len(close), dtype=bool); tr = np.full(len(close), np.nan)
in_pos, peak = False, np.nan
for i in range(len(close)):
    if not in_pos:
        if entry[i]:
            in_pos, peak = True, high[i]
    else:
        peak = np.maximum(peak, high[i])
        if close[i] < peak * (1 - pct / 100):
            in_pos, ex[i] = False, True
    tr[i] = peak if in_pos else np.nan
@exit, @trail = ex, tr

# bar mode
state.in_pos: bool = False
state.peak: float = nan
if not state.in_pos:
    if @entry:
        state.in_pos = True
        state.peak = @high
else:
    state.peak = max(state.peak, @high)
    if @close < state.peak * (1 - chf("trail_pct", default=5.0) / 100):
        state.in_pos = False
        @exit: bool = True
@trail = state.peak if state.in_pos else nan
```

**E5. Helpers per bar** (tier: vectorized; the two helpers are hoisted)

```python
# column mode
@signal = sl.crosses_above(@close, sl.ema(@close, 20)) & (sl.rsi(@close, 14) < 70)

# bar mode
if sl.crosses_above(@close, sl.ema(@close, 20)) and sl.rsi(@close, 14) < 70:
    @signal = True
```

**E6. A recursive filter** (tier: compiled, because `@smooth[-1]` reads its own last value)

```python
# column mode (near, not equal: see below)
a = chf("alpha", default=0.2)
@smooth = @close.ewm(alpha=a, adjust=False).mean()

# bar mode
a = chf("alpha", default=0.2)
prev = @smooth[-1]
@smooth = @close if isnan(prev) else a * @close + (1 - a) * prev
```

Measured (`e6_ewm_check.py`): pandas `ewm` differs from this recursion in the last bits (up to 7e-13 on prices near 100), because it computes in a different order. It also skips NaN. So E6's equality test uses a column-mode loop, as in E4. Bar mode shows the exact rule.

**E7. Count rising bars in the last n** (tier: compiled, a loop with a computed history index)

```python
# column mode
n = chi("n", default=5)
@ups = sum((sl.shift(@close, k) > sl.shift(@close, k + 1)).astype(float) for k in range(n))

# bar mode
ups = 0
for k in range(chi("n", default=5)):
    if @close[-k] > @close[-k - 1]:
        ups += 1
@ups = ups
```

---

## 5. Speed: three tiers

### 5.1 How the tier is chosen

- `prepare()` picks the tier **once**, from the parsed tree, when the code is saved or typed. It never decides per bar or per tick. The order is vectorized, then compiled, then Python loop.
- The pick is stored on `PreparedCode` (`tier`, `tier_reason`). `parse_code` returns it, so the badge shows before any cook.
- The only later change is a **one-time demotion**. If numba fails to compile at the first cook, the snippet drops from compiled to Python loop. That is recorded once per (snippet, input dtypes) and reported with an info diagnostic. The same demotion then applies to every later cook, bots included.

### 5.2 Tier (a) vectorized: exactly which snippets qualify

A bar-mode snippet is vectorized when all of these hold:

1. **Statements:** only assignments to `@attr` or to a local, augmented assignments (`+=` and so on), `if`/`elif`/`else` holding only these, and `pass`.
2. **No** `for`, `while`, `return`, `break`, `continue`, `try` or setup lines, and **no** `state`.
3. **No self-history.** Nothing reads `@x[-k]` with `k >= 1` for an `@x` the snippet writes.
4. **History indexes** are int literals of 0 or less, or a constant `chi()` name.
5. **Expressions:** numbers, `True`, `False`, `nan`, attribute reads, history reads, locals, `ch*()`, hoisted `sl.*`/`ta.*` calls, `+ - * / // %`, `x ** 2` (rewritten as `x * x` in every tier), unary `-`, comparisons (chains too), `and`/`or`/`not`, `x if c else y`, `abs`, `min`, `max`, `round` and `isnan`.
6. **Exactly-rounded math only.** `sqrt`, `floor`, `ceil` and `abs` qualify. `log`, `exp`, the trig functions and any other `**` do not; they send the snippet to the compiled tier. There every `**` calls the C library's `pow`, as the Python tier does. numba's own integer-power loop would round differently (section 6.2).

**The rewrite** (it works on the tree; the prototype is `proto.py`, `Analysis.vector_source`):

| One bar | Column code |
|---|---|
| `a > b`, `a + b` | the same operator, broadcast over the columns |
| `a and b`, `a or b`, `not a` | `a & b`, `a \| b`, `~a`, with each operand made bool first (section 6.2) |
| `a < b < c` | `(a < b) & (b < c)` |
| `x if c else y` | `np.where(c, x, y)` |
| `if c: @x = v` | `m = c`, then `x = np.where(m, v, x_before)`; nested ifs AND their masks; `elif`/`else` use `~m` |
| `@close[-n]` | a shifted column with NaN (float) or `False` (bool) for the first n bars, computed once per `(name, n)` |
| `max`, `min`, `abs`, `round` | `np.maximum`, `np.minimum`, `np.abs`, `np.round` |

- Every branch is computed on every bar under `np.errstate(all="ignore")`. Then the mask picks the result. This is safe because bar-mode math never raises (section 6.2) and hoisted helpers are pure.
- The prototype rewrite takes about 0.8 ms per snippet. That is small enough to run on every keystroke in `parse_code`.

### 5.3 Tier (b) compiled: numba

- **What it covers:** everything in 5.2 plus `state`, self-history, `for` over `range(...)`, `while`, `break`, `continue`, `return`, int locals, computed history indexes, and the full `math` set. Setup `import`/`def` lines, containers, strings and `try` are not covered; they go to the Python loop.
- **The generated kernel** is one function, `kernel(start, end, <inputs>, <hoisted>, <ch values>, <state in>)`, returning the outputs and `<state out>`. E4 becomes about 20 lines (`proto.py`, `loop_source`). Options: `@njit(cache=True, error_model="numpy", fastmath=False, boundscheck=False)`. Bounds checks can stay off because every history read is guarded (`x[i-k] if i >= k else NAN`).
- **Cold compile cost** (measured, 5.6): about 0.2 s once per backend process to start numba, then 37 to 130 ms per snippet. Later calls take microseconds. Importing numba costs nothing extra: pandas-ta already imports it when Wave 7 loads (156 ms in a bare process).
- **Disk cache.** Numba's `cache=True` needs a real file, so code made with `exec` cannot be cached.
  - Write the kernel to `backend/data/code_jit_cache/bm_<sha16>.py` (the sha covers the generated source and the numba and Python versions). Set `NUMBA_CACHE_DIR` to the same folder, and add it to `.gitignore`.
  - Import it with `importlib` and **register it in `sys.modules` first**. Without that, a new process cannot load the cache (`ModuleNotFoundError: No module named '<dynamic>'`); the prototype hit this.
  - Measured: a cached kernel loads in 1.2 to 1.6 ms in a fresh process.
- **Warm-up.** Compile in the background, never inside a bot tick's 10 s guard: on bot start, spawn, `graph_update` and the save of a code-bearing graph. Start numba once in a background thread when the backend starts. A cook that arrives before its warm-up compiles inside the guard, which costs at most about 0.35 s.
- **When numba fails to compile** (a `TypingError`, measured at about 70 ms): the snippet runs as a Python loop, with the same results by contract (section 6). The badge turns amber. An info diagnostic `bar_tier` points at the rejected user line, through a map from kernel lines to user lines.
- **A crash inside numba** (rare; a numba bug) is the same accepted risk as a crash inside numpy (W7 note 4.7). `SL_CODE_JIT=0` (section 8) turns the tier off for that case.
- **Pin numba.** Add `numba==0.61.2` to `backend/requirements.txt`. Today it arrives only as a pandas-ta dependency, so a pandas-ta upgrade could drop or move it.

### 5.4 Tier (c) Python loop

- **What runs:** the same generated loop as tier (b), as plain Python, reading Python lists (`array.tolist()`). Lists measured 1.2 to 2 times faster than reading numpy scalars. The exception is snippets dominated by hoisted helpers, where converting to lists costs more.
- Division, `//`, `%`, `**` and the `math` calls go through small helpers with numpy's rules. Plain Python floats would raise `ZeroDivisionError` where numpy and numba return inf.
- **Cost guard (per-bar cost limit).** The loop runs in chunks of 16,384 bars and checks the cook guard between chunks, so a cancelled cook stops and leaks no thread. After the first chunk it projects the total time. If that is longer than the time left in the guard (10 s bot, 60 s route), it fails early with `code_timeout`: "about 95 s for 500,000 bars; the limit is 60 s". Each cook records the ns per bar for the badge tooltip.
- **Loop budget, all loop tiers.** Every user `for`/`while` gets a hidden counter. More than 1,000,000 passes on one bar raises `code_runtime`: "a loop ran more than 1,000,000 times on bar 1234". This matters most for compiled code, which cannot be stopped from outside.

### 5.5 The badge

| Badge | Colour | Tooltip example |
|---|---|---|
| vectorized | green | "vectorized: runs as column code. Show column code" |
| compiled | blue | "compiled: line 1 keeps state (state.in_pos). First run compiles (about 0.1 s)" |
| python loop | amber | "python loop: line 4 uses a dict, which numba cannot compile. 170 ns per bar" |

Column-mode code shows no badge.

### 5.6 Measured table

- **Machine:** this Mac, Apple M1, 8 cores, 16 GB, macOS (Darwin 27). Python 3.12.14, numpy 2.2.6, pandas 3.0.2, numba 0.61.2 (`backend/venv`). Run on 2026-10-03.
- **Data:** synthetic OHLCV with about 0.2 % NaN closes, plus an RSI column and a sparse bool entry column.
- **Scripts** (kept locally in `.run/F435/barmode/`, which git ignores; the results are recorded here). Each was run with `backend/venv/bin/python`.
  - `proto.py` is a **prototype** translator (vectorize and loop codegen), not production code. `shiftlib.py` and `snippets.py` hold the history helpers, the snippets and the frame builder.
  - `bench.py` (`bench.out`) gives warm timings and exact-equality checks. `bench_numba_cold.py` (`bench_numba_cold.out`) gives cold compile and cache cost, each in a fresh process. `numba_fail_probe.py` times a numba refusal. `e6_ewm_check.py` is the E6 check.
- **The snippets.** S1 is E1 (writing `@signal = 1`, a float), S2 is E2, S3 is E4 (with `@exit = False` spelled out) and S4 is E5.
- **Method.** Times are medians in ms and include hoisting. "column" is the hand-written column-mode reference; for S3 it is a Python loop, because no vector form exists. Every tier matched the reference exactly: values, NaN positions and dtype.

| Snippet | Bars | column | (a) vectorized | (b) compiled, warm | (c) loop, numpy reads | (c) loop, list reads |
|---|---|---|---|---|---|---|
| S1 pure condition | 5,000 | 0.01 | 0.01 | 0.01 | 0.70 | 0.50 |
| S2 history `@close[-1]` | 5,000 | 0.08 | 0.04 | 0.02 | 1.76 | 0.81 |
| S3 running state | 5,000 | 0.68 | n/a | 0.01 | 1.21 | 0.76 |
| S4 `sl` helpers (hoist 0.72) | 5,000 | 0.71 | 0.73 | 0.76 | 0.92 | 1.14 |
| S1 pure condition | 500,000 | 0.72 | 0.76 | 2.98 | 70.3 | 59.6 |
| S2 history `@close[-1]` | 500,000 | 4.00 | 2.73 | 2.54 | 176.5 | 84.6 |
| S3 running state | 500,000 | 71.2 | n/a | 0.92 | 123.0 | 82.5 |
| S4 `sl` helpers (hoist 14.5) | 500,000 | 13.1 | 14.2 | 14.0 | 34.6 | 51.7 |

**Numba cold start** (fresh processes, 5,000 bars):

| Step | S1 | S2 | S3 | S4 |
|---|---|---|---|---|
| Once per process: start numba (first compile of any kernel) | 0.19 to 0.23 s | | | |
| First call, no cache (compile and run) | 130 ms | 61 ms | 121 ms | 37 ms |
| First call, `cache=True`, first process (compile and write) | 133 ms | 65 ms | 127 ms | 40 ms |
| First call, `cache=True`, new process (load) | 1.6 ms | 1.2 ms | 1.4 ms | 1.3 ms |
| Later calls | 0.024 ms | 0.021 ms | 0.013 ms | 0.008 ms |

**What the numbers say**

- Vectorized and compiled code are about equally fast. For S1 to S3, both are 20 to 130 times faster than the Python loop at 500,000 bars.
- For state (S3), the compiled tier is about 75 times faster than the best a column-mode user can write without numba.
- When hoisted helpers dominate (S4), the tier hardly matters. The compiled tier was slower than vectorized on the branchy S1 (3.0 against 0.8 ms). So vectorized stays first in the order.
- A single backtest is fast in every tier. The Python loop costs real time in the optimizer and walk-forward runs, which cook hundreds of times. That is where the tiers pay off.
- These numbers are from an M1. The production VM is x86 and will differ. The test suite records the ns per bar on both.

---

## 6. Exact equivalence

### 6.1 The rule

Bar mode gives **bit-identical** results to the same logic in column mode, in every tier. "The same logic" is the vectorize rewrite in 5.2, which "Show column code" displays. For snippets that cannot be vectorized, the Python loop tier is the reference, and the compiled tier must equal it.

### 6.2 NaN, bool, float and math rules (all tiers)

- **Comparisons** with NaN are `False` (`!=` is `True`). Python, numpy and numba already agree.
- **Truth of a float** in `if x:`, `and`, `or` and `not` is `x != 0`. So NaN counts as true, as in Python. Write a comparison when you mean one.
- **`and` and `or` always give a bool.** Python returns one of the operands instead. Bar mode changes this in every tier, so that `&` and `|` can stand in for it.
- **Arithmetic** follows numpy. NaN in gives NaN out. `x / 0` is ±inf or NaN and never an error. `(-8) ** (1/3)` is NaN, not a complex number. This needs numba's `error_model="numpy"` and the Python-tier helpers.
- **`max` and `min`** pass NaN through (`np.maximum` rules). Python's own `max(nan, 1)` depends on argument order, so bar mode replaces it.
- **`round(x, n=0)`** follows `np.round`: half to even, a float result, NaN kept, and numpy's multiply-and-round method for `n > 0`. Python's `round(nan)` raises, so bar mode replaces it too.
- **History before bar 0:** NaN for float and `False` for bool. A bool `@flag[-1]` equals column mode's `sl.shift(@flag, 1) == 1`.
- **Unwritten bars:** the start value (section 3.3).
- **Ints:** locals are 64-bit in the compiled tier. Python ints do not overflow. They differ only beyond 9.2e18, which is documented and not tested.
- **Transcendental math** (`log`, `exp`, the trig functions, `**` other than `** 2`). The compiled and Python tiers both call the C math library, so they agree bit for bit. numpy's vector math on some x86 CPUs (AVX-512) can differ from it in the last bit, so section 5.2 keeps these out of the vectorized tier. A hand-written column-mode `np.log` on such a CPU may differ from bar mode in the last bit. This is the one honest exception to the rule.
- **No fused multiply-add.** `fastmath=False` keeps numba from merging `a * b + c` into one step, which would round differently. The property tests guard against this.

### 6.3 Tests

- **Property tests** in `backend/tests/nodebuilder/code_nodes/test_bar_equivalence.py`. A seeded generator builds random snippets from the 5.2 grammar (bounded depth), plus `state` and loop shapes for the compiled tier. Random frames have NaN gaps, bool columns and lengths 0, 1, 2, 3, 100 and 5,000. Every tier is checked against the Python loop with `np.array_equal(..., equal_nan=True)` and a dtype check. Seeds are fixed, so a failure repeats. hypothesis is not installed, so this adds no dependency.
- **Shared vectors** in `backend/tests/nodebuilder/vectors/bar_mode.json`. Each vector has a snippet, a tiny frame, the expected outputs, the expected tier and the expected diagnostics with positions. Vitest reads the tier and position fields for the badge and the markers.
- **Column pairs:** E1 to E7 run in column mode through the Wave 7 `run()` and in bar mode. They must be equal exactly (E6 against a column-mode loop).
- **Tier pins.** E1, E2, E3 and E5 must vectorize. E4, E6 and E7 must compile. A dict snippet must run as a Python loop. `SL_CODE_JIT=0` forces the Python loop and gives the same numbers.

---

## 7. Diagnostics

- Positions follow Wave 7: `line` 1-based, `col` 0-based, in characters of the user's text, mapped through the sugar column map.
- **Prepare-time errors** (`bar_lookahead`, `bar_unsupported`, `code_type`, `code_syntax`) are found on the user's own tree, so their positions are exact.
- **Generated code keeps positions.** The vectorized and Python-loop code is built as a tree. Each new node copies the line and column of the user node it came from (`ast.copy_location`). It is compiled with the snippet's Wave 7 filename (`<code:{node_id}>`). So Wave 7's traceback-to-diagnostic mapping works unchanged.
- **Errors in compiled code.** Numba exceptions carry no user position. On any exception from a compiled kernel, the runtime **replays the same bars in the Python loop tier**. Because the tiers are equivalent, the same error happens on the same bar, now with an exact position. The replay costs at most one Python-loop run, and only on failure. Loop-tier errors name the bar: "code_runtime: line 4: ... on bar 1234 (2024-03-05 10:30)".
- **New codes** are additive. They need to be registered in `SEVERITY_BY_CODE` and in the frontend union.

| Code | Severity | When |
|---|---|---|
| `bar_lookahead` | error | a positive history index (literal at prepare time, computed at run time) |
| `bar_unsupported` | error | a construct refused in bar mode (3.9), an undeclared `state`, a local that may be unset, a per-bar argument to a hoisted helper |
| `bar_tier` | info | the tier and its reason, including a numba demotion and the user line it points at |
| `bar_history_lookback` | warning | a literal history index deeper than `lookback_bars` |

---

## 8. Wave 7 guards

- **Timeouts.** The same 10 s (bot) and 60 s (route) guards apply. The Python loop stops between chunks when cancelled, so it leaks no thread. Compiled kernels cannot be stopped from outside; like numpy C code, they leak until they end. The loop budget (5.4) keeps that rare.
- **Kill switch.** `SL_CODE_NODES=0` turns off bar mode too. Bar-mode code is code. A new switch, `SL_CODE_JIT=0`, only turns off the compiled tier (the Python loop runs instead). It is for a numba incident and changes speed only, never results.
- **Audit.** The audit line is unchanged for column mode. A bar-mode snippet adds `mode=bar` at the end. The sha256 still covers the user's source only, so the mode field tells two identical texts apart.
- **Bots.** A bar-mode snippet in a live bot uses the same `PreparedCode`, so it runs the same tier as the backtest. That includes any recorded demotion, because both run in the same backend process. The bot log records the tier at start. Even if tiers differed, the results would not (section 6).
- **Cook cache key.** `eval_hash` adds `code_mode` to a node's row **only when it is `"bar"`**, so every existing hash stays the same. The `prepare()` cache key adds the mode.
- **Lookback.** `lookback_bars` still sizes the bot's fetch window. `state` and self-history depend on where the window starts: a trailing high from an entry older than the window will differ live. So `bar_tier` on a state-bearing snippet adds "keeps state; set lookback_bars to cover your longest trade". The live-parity test gets a bar-mode state fixture.
- **Rollback.** An older backend ignores `code_mode` (Pydantic drops unknown fields). It would run bar code as column code, and that fails loudly: `if <Series>` raises "truth value is ambiguous". It does not trade silently on the wrong values.

---

## 9. The VEX feel, honestly

**Will feel like VEX**
- [x] Write one bar; the loop is implied. `@close` is a number; `if`/`else` on attributes; `@x = ...` writes.
- [x] A new attribute starts at 0, and an existing one keeps its value.
- [x] `ch()` and auto-promoted params, as in Wave 7.
- [x] "Run Over" on the node, like the Attribute Wrangle. `return` ends the current bar.
- [x] Fast by default. Most signal snippets vectorize or compile, as VEX compiles.

**Will not feel like VEX**
- [ ] No type prefixes (`f@`, `i@`). Python reads `f@x` as matrix multiply. Annotations do the job instead.
- [ ] Python syntax: colons, indentation, `and`/`or`/`not` rather than `&&`/`||`/`!`, no semicolons, no braces.
- [ ] No vectors or matrices per bar (`v@P`). A bar has scalar attributes only.
- [ ] History (`@close[-1]`) and `state` are not in VEX. VEX points cannot see each other's new values; bars can see the past.
- [ ] The first run of compiled code waits about 0.1 to 0.35 s. VEX compiles too fast to notice.
- [ ] A snippet outside the fast subset runs as a slow Python loop instead of failing to compile. The badge is the only warning.
- [ ] `and`/`or` give a bool, and `max`/`min`/`round` follow numpy. This is safer, but it differs from plain Python.

---

## 10. Implementation plan (future item: Wave 8, bar mode)

**Precondition:** Wave 7 merged and deployed. No Wave 7 file changes behaviour in column mode.

| Item | What | Owns | New files |
|---|---|---|---|
| B.1 | Bar-mode analysis and the vectorize rewrite: tree checks (3.1 to 3.9), type inference, `state` declarations, definite assignment, hoisting, tier choice, the vectorize codegen with shared history columns and position copying. `prepare(..., mode=)` and the new `PreparedCode` fields `mode`, `tier`, `tier_reason` and `plan`. The new codes. | `backend/nodebuilder/code/runtime.py` (the `mode` keyword, cache key and dispatch only), `__init__.py` (exports), `backend/nodebuilder/diagnostics.py` (4 codes) | `backend/nodebuilder/code/bar/{__init__,analyze,vectorize,helpers}.py`, `backend/tests/nodebuilder/code_nodes/{test_bar_analyze,test_bar_vectorize}.py`, `vectors/bar_mode.json` |
| B.2 | The loop codegen shared by the compiled and Python tiers: chunked driver, guard checks and projection, loop budget, numba compile with the disk cache and `sys.modules` registration, demotion, `SL_CODE_JIT`, background warm-up, error replay for positions. Pin numba. | `backend/requirements.txt`, `.gitignore` | `backend/nodebuilder/code/bar/{loopgen,jit}.py`, `test_bar_loop.py`, `test_bar_jit.py`, `test_bar_equivalence.py` |
| B.3 | Wiring: the `Node.code_mode` model field, `eval_hash` (bar only), `parse_code` `mode` in and `tier`/`tier_reason` out, `code_capabilities` gains `modes` and `jit`, the audit `mode=bar` field, the bot-start tier log line, warm-up on bot start, spawn, save and `graph_update`. | `backend/nodebuilder/models.py`, `cook_cache.py`, `backend/routes/nodebuilder.py`, `code/audit.py`, `backend/bot_manager.py` (warm-up and log only) | `backend/tests/nodebuilder/test_bar_mode_levels.py`, additions to `test_code_live_parity.py` (bar-mode state fixture) |
| B.4 | UI: the Run Over switch in the drawer and the Wrangle body, the tier badge with tooltip, "Show column code", the `bar_*` markers, and Monaco highlighting for `state.` and `@x[-n]`. | `code/CodeDrawer.tsx`, `nodes/WrangleNode.tsx`, `frontend/src/api/nodebuilderCode.ts` (additive fields) | `code/TierBadge.tsx`, `__tests__/barMode.test.tsx` |

**Order.** B.1, then B.2, then B.3. B.4 runs in parallel from the start against the `parse_code` additions.

**Contract additions** (all optional, with Wave 7 defaults):

```text
POST /api/nodebuilder/parse_code   body adds  "mode": "column" | "bar"   (default "column")
  -> 200 adds "tier": "vectorize" | "jit" | "loop" | null,  "tier_reason": str | null
GET  /api/nodebuilder/code_capabilities  adds  "modes": ["column", "bar"],
                                                "jit": {"available": true, "numba": "0.61.2"}
Graph JSON node                    adds  "code_mode": "column" | "bar"  (omitted = "column")
```

**Acceptance (scripted)**
- The property suite passes with 200 seeds per tier. E1 to E7 match their column-mode pairs exactly. The tier pins in 6.3 hold.
- A bar-mode E4 runs on 500,000 synthetic bars in under 10 ms warm, measured by the test.
- `@close[1]` gives `bar_lookahead` at the index. `print(@close)` gives `bar_unsupported`.
- A forced numba `TypingError` demotes to the Python loop, with the same output and a `bar_tier` info at the user's line.
- A compiled kernel that divides by zero gives inf, not an error. A compiled-kernel exception is reported at the user's line through the replay.
- The Wave 7 suite and the parity trio still pass with no snapshot regeneration. `eval_hash` is unchanged for every column-mode fixture.
- `npm --prefix frontend run build` passes.

**Estimate.** Wave 7's item 7.A is about 2,800 lines of runtime and 1,500 lines of tests (228 tests). Bar mode is about 1,500 backend lines (analysis 450, vectorize 350, loop codegen 350, jit 250, helpers 100), 1,200 test lines and 300 UI lines. That is roughly 60 % of 7.A plus a small slice of 7.D: two backend implement-and-review cycles and one frontend cycle, about one orchestrated day. The risky part is the property suite (6.3); most fix rounds will likely come from it.

---

## 11. Open questions for John

1. **Start value of a new attribute on bars the code does not write: `0`/`False` (VEX) or NaN?** *Recommendation: 0/False, as VEX does.* It makes `if cond: @signal = True` work with no extra line, and `@x = nan` on the first line gives NaN when you want it.
2. **Should bar mode accept any Python, running it as a slow Python loop, or refuse what cannot vectorize or compile?** *Recommendation: accept it, with the amber badge and the ns-per-bar tooltip.* This keeps your Wave 7 decision (real Python, nothing blocked), and the badge makes the cost visible.
3. **Should bar mode later read the real position (bars in trade, entry price, open profit) as read-only attributes, once the W8 position-state nodes exist?** *Recommendation: yes, but as part of the W8 position-state work, not this item.* Position state comes from the simulator. Until then, `state` covers signal-side memory such as E4.
