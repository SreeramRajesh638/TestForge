# Testing your own code with mutations (hands-on guide)

All commands run from the project folder (on Windows: open the folder, type `cmd` in the address
bar, press Enter, then run `.venv\Scripts\activate` once).

## Way 1: the dashboard (easiest)

1. `testforge serve` (or double-click `run_dashboard.bat`) and open http://127.0.0.1:8000
2. Paste your Python code into **Code under test** and give it a **Module name** (e.g. `mycode`).
3. Choose a backend (Groq = free AI, Mock = no AI) and click **Generate tests**.
4. Look at the **Surviving mutants** tab: each row is a planted bug the tests did not catch.

## Way 2: let the AI write tests for your file

```bash
testforge generate path/to/mycode.py --llm groq --effort medium
```

The tests and a JSON report land in `generated_tests/`.

## Way 3: write the tests yourself and let mutations judge them

This is the best way to *learn* what mutation testing shows. The tutorial files are in
`examples/tutorial/`.

**1. See every mutant TestForge creates for your code**

```bash
testforge mutants examples/tutorial/discount.py
```

```
11 mutants in examples/tutorial/discount.py: AOR 1, CRP 5, LCR 1, RET 2, ROR 2
  #4 [ROR] line 5 in discount: `price >= 100` -> `price > 100`
  ...
```

Filter by kind with `--operator ROR,AOR`, look at one as a diff with `--show 4`, or write one to a
file with `--write 4` (creates `discount_mutant4.py`) to run your tests against it by hand.

| Operator | Plants a bug by... | Example |
|---|---|---|
| AOR | swapping arithmetic | `a * b` -> `a / b` |
| ROR | swapping comparisons | `x >= 100` -> `x > 100` |
| LCR | swapping and/or | `a and b` -> `a or b` |
| UOI | removing `not` / unary minus | `not x` -> `x` |
| CRP | changing a constant | `100` -> `101`, `"msg"` -> `""` |
| RET | returning None | `return x` -> `return None` |
| ASR | swapping `+=` style operators | `x += 1` -> `x -= 1` |

**2. Score a weak test suite**

```bash
cd examples/tutorial
testforge mutate discount.py test_discount_weak.py --all
```

```
Tests 2/2 passing, line 83%, branch 75%
Mutation score 45.5% (5/11)
  survived  #3 [CRP] line 5 in discount: `100` -> `101`
  survived  #4 [ROR] line 5 in discount: `price >= 100` -> `price > 100`
  ...
```

High coverage, yet more than half the bugs go unnoticed: there is no test at the boundary
(exactly 100), none for the error message and none for rounding.

**3. Add a test for each survivor and score again**

```bash
testforge mutate discount.py test_discount_strong.py --all
```

```
Tests 7/7 passing, line 100%, branch 100%
Mutation score 100.0% (11/11)
```

Rules worth knowing:

* Your tests must **pass on the original code** first; otherwise TestForge refuses to score them.
* Some survivors can be **equivalent mutants**: no test can ever tell them apart from the original
  (e.g. `x < low` -> `x <= low` in a clamp function that returns `low` either way). Leave those.
