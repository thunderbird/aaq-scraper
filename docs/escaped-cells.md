# Escaped cells (CSV formula-injection normalization)

Audit of the one-time back-fill performed by `normalize_csv_escaping.py` (merged in PR #11, commit `1044044`). Each listed cell began with a spreadsheet-formula character and was prefixed with a single quote (`'`) so spreadsheets treat it as text. Values shown are the **original** (pre-escape) cell, truncated to 80 chars; each now has a leading `'`.

- **Files changed:** 65
- **Cells escaped:** 171
- **By leading character:** `@` ×171
- **By column:** `creator` ×150, `solved_by` ×16, `updated_by` ×4, `involved` ×1

Regenerate from source data with `normalize_csv_escaping.py` (idempotent).


## `2026/answers-thunderbird-desktop-2026-03-23.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1807546 | creator | `@next` |
| 1807547 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-02.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1815370 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-03.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1815626 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-04.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1815776 | creator | `@next` |
| 1816137 | creator | `@next` |
| 1816139 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-05.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1816151 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-06.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1816270 | creator | `@next` |
| 1816336 | creator | `@next` |
| 1816507 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-07.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1816508 | creator | `@next` |
| 1816554 | creator | `@next` |
| 1816574 | creator | `@next` |
| 1816576 | creator | `@next` |
| 1816632 | creator | `@next` |
| 1817035 | creator | `@next` |
| 1817043 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-08.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1816782 | creator | `@next` |
| 1816843 | creator | `@next` |
| 1816846 | creator | `@next` |
| 1816847 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-09.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1818056 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-11.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1817726 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-12.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1818054 | creator | `@next` |
| 1818100 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-13.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1818206 | creator | `@devarmenchik` |

## `2026/answers-thunderbird-desktop-2026-05-14.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1818304 | creator | `@next` |
| 1818310 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-15.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1818516 | creator | `@next` |
| 1818628 | creator | `@-my-wits-end` |

## `2026/answers-thunderbird-desktop-2026-05-16.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1818707 | creator | `@next` |
| 1818734 | creator | `@next` |
| 1818755 | creator | `@next` |
| 1818756 | creator | `@next` |
| 1818782 | creator | `@next` |
| 1819102 | creator | `@next` |
| 1819406 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-17.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1818909 | creator | `@next` |
| 1818929 | creator | `@next` |
| 1818936 | creator | `@next` |
| 1818956 | creator | `@next` |
| 1818962 | creator | `@next` |
| 1819339 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-18.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1819099 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-19.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1819617 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-20.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1819644 | creator | `@next` |
| 1819731 | creator | `@next` |
| 1819733 | creator | `@next` |
| 1819734 | creator | `@next` |
| 1819763 | creator | `@next` |
| 1819764 | creator | `@next` |
| 1819900 | creator | `@next` |
| 1819910 | creator | `@next` |
| 1819924 | creator | `@next` |
| 1819931 | creator | `@next` |
| 1820009 | creator | `@next` |
| 1820145 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-21.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1820007 | creator | `@next` |
| 1820013 | creator | `@next` |
| 1820246 | creator | `@next` |
| 1820261 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-22.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1820158 | creator | `@next` |
| 1820177 | creator | `@next` |
| 1820179 | creator | `@next` |
| 1820244 | creator | `@next` |
| 1820260 | creator | `@next` |
| 1820278 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-25.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1820987 | creator | `@next` |
| 1820988 | creator | `@next` |
| 1821302 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-26.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1821046 | creator | `@next` |
| 1821185 | creator | `@next` |
| 1821298 | creator | `@next` |
| 1821303 | creator | `@next` |
| 1821941 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-27.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1821294 | creator | `@next` |
| 1821343 | creator | `@next` |
| 1821344 | creator | `@next` |
| 1821347 | creator | `@next` |
| 1821417 | creator | `@next` |
| 1821422 | creator | `@next` |
| 1821441 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-28.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1821536 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-30.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1822124 | creator | `@next` |
| 1822492 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-05-31.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1822491 | creator | `@next` |
| 1822527 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-03.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1822902 | creator | `@next` |
| 1822932 | creator | `@next` |
| 1822993 | creator | `@next` |
| 1822996 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-04.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1823095 | creator | `@next` |
| 1823210 | creator | `@next` |
| 1823401 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-05.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1823352 | creator | `@next` |
| 1823368 | creator | `@next` |
| 1823436 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-06.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1823554 | creator | `@next` |
| 1823620 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-07.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1823777 | creator | `@next` |
| 1823808 | creator | `@next` |
| 1824633 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-08.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1824069 | creator | `@next` |
| 1824072 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-09.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1824076 | creator | `@next` |
| 1824082 | creator | `@next` |
| 1824616 | creator | `@next` |
| 1824619 | creator | `@next` |
| 1824622 | creator | `@next` |
| 1824627 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-10.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1824611 | creator | `@next` |
| 1824612 | creator | `@next` |
| 1824615 | creator | `@next` |
| 1824618 | creator | `@next` |
| 1824624 | creator | `@next` |
| 1824661 | creator | `@next` |
| 1824692 | creator | `@next` |
| 1824756 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-11.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1824690 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-12.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1824948 | creator | `@next` |
| 1824965 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-15.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1825613 | creator | `@next` |
| 1825716 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-16.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1825730 | creator | `@next` |
| 1825748 | creator | `@next` |
| 1825948 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-17.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1825949 | creator | `@next` |
| 1826003 | creator | `@next` |
| 1826055 | creator | `@next` |
| 1826082 | creator | `@next` |
| 1826647 | creator | `@next` |
| 1826649 | creator | `@next` |
| 1826652 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-19.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1826643 | creator | `@next` |
| 1826644 | creator | `@next` |
| 1826653 | creator | `@next` |
| 1826701 | creator | `@next` |
| 1826702 | creator | `@next` |
| 1826704 | creator | `@next` |
| 1826716 | creator | `@next` |
| 1826728 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-20.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1826956 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-21.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1827003 | creator | `@next` |
| 1827017 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-22.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1827585 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-23.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1827835 | creator | `@next` |
| 1827836 | creator | `@next` |

## `2026/answers-thunderbird-desktop-2026-06-24.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1827891 | creator | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-03.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1579753 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-04.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1579858 | updated_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-07.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1580411 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-11.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1581154 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-15.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1581773 | creator | `@-my-wits-end` |
| 1581773 | involved | `@-my-wits-end;MattAuSupport;` |

## `2026/questions-thunderbird-desktop-2026-05-17.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1582104 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-20.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1582681 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-22.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1583125 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-25.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1583774 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-05-31.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1584880 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-03.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1585354 | solved_by | `@next` |
| 1585419 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-04.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1585530 | solved_by | `@next` |
| 1585577 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-09.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1586412 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-10.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1586647 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-16.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1587683 | updated_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-19.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1588351 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-21.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1588755 | solved_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-23.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1589174 | updated_by | `@next` |

## `2026/questions-thunderbird-desktop-2026-06-24.csv`

| row id | column | original value (pre-escape) |
|---|---|---|
| 1589326 | updated_by | `@next` |
