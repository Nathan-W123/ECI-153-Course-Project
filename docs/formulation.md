# Mathematical formulation

A 0–1 integer linear program that assigns one weekly meeting pattern to every course component.
It follows the course's formulation slides, which build on Rappos et al. (2022),
*A mixed-integer programming approach for solving university course timetabling problems*,
Journal of Scheduling 25, 391–404, <https://doi.org/10.1007/s10951-021-00715-5>.

## Sets

| Symbol | Meaning | Code |
|---|---|---|
| $C$ | schedulable components (lectures, labs, discussions) | `inputs.components` |
| $P_c$ | feasible patterns of $c$: its family's patterns minus those that break a hard request | `data.P[c]` |
| $P_c^{NP} \subseteq P_c$ | non-prime patterns of $c$ | `patterns[p].nonprime` |
| $V$ | soft-request cases: one per (soft request, component it applies to) | `data.soft_cases` |
| $B_v \subseteq P_{c(v)}$ | patterns that violate case $v$ | `soft_cases[v].violating` |
| $\mathcal{I}$ | unordered component pairs that share an instructor | `data.I` |
| $\mathcal{H}$ | (child, parent) pairs, e.g. (lab, its lecture) | `data.H` |
| $\mathcal{G}_j$ | unordered pairs inside conflict group $O_j$ | `data.G` |
| $\Omega_{cc'}$ | $\{(p,p') \in P_c \times P_{c'} : p, p' \text{ overlap}\}$ | `data.C2/C3/C5_indices` |
| $D$ | departments | |
| $C_d^R, C_d^L$ | department $d$'s components counting toward non-prime, enrollment $\le 150$ / $> 150$ | `data.np_sets` |

## Parameters

| Symbol | Meaning | W27 value |
|---|---|---|
| $W_O$ | global weight on conflict-group overlaps | 100 |
| $W_V$ | global weight on soft-request violations | 20 |
| $w_j$ | weight of conflict group $j$ | 1, 3 or 5 |
| $w_v$ | weight of soft request $v$ | 1–4 |
| $r_R, r_L$ | required non-prime share, regular / large components | 0.35 / 0.30 |

## Decision variables

$$
x_{cp} = 1 \text{ if component } c \text{ uses pattern } p \in P_c, \qquad
y_v = 1 \text{ if case } v \text{ is violated}, \qquad
o_{jcc'} = 1 \text{ if } c, c' \text{ overlap in group } j.
$$

## Model

$$
\min Z = W_O \sum_{j} w_j \sum_{\{c,c'\} \in \mathcal{G}_j} o_{jcc'} + W_V \sum_{v \in V} w_v\, y_v
$$

subject to

| | Constraint | For all |
|---|---|---|
| C1 | $\sum_{p \in P_c} x_{cp} = 1$ | $c \in C$ |
| C2 | $x_{cp} + x_{c'p'} \le 1$ | $\{c,c'\} \in \mathcal{I},\ (p,p') \in \Omega_{cc'}$ |
| C3 | $x_{cp} + x_{c'p'} \le 1$ | $(c,c') \in \mathcal{H},\ (p,p') \in \Omega_{cc'}$ |
| C4 | $y_v \ge x_{c(v)p}$ | $v \in V,\ p \in B_v$ |
| C5 | $x_{cp} + x_{c'p'} - o_{jcc'} \le 1$ | $j,\ \{c,c'\} \in \mathcal{G}_j,\ (p,p') \in \Omega_{cc'}$ |
| C6 | $\sum_{c \in C_d^R} \sum_{p \in P_c^{NP}} x_{cp} \ge \lceil r_R \lvert C_d^R \rvert \rceil$ | $d \in D$ |
| C7 | $\sum_{c \in C_d^L} \sum_{p \in P_c^{NP}} x_{cp} \ge \lceil r_L \lvert C_d^L \rvert \rceil$ | $d \in D$ |

$y$ and $o$ only need lower bounds: because they are minimized, they are 0 unless a constraint forces 1.

## Conventions used in the implementation

- **Time intervals are half-open** `[start, end)`. A meeting ending at 10:00 does not overlap one
  starting at 10:00. The validation dataset depends on this: its only open lecture slot (MWF 10:00)
  touches a FixedSchedule block that ends at 10:00.
- **Non-prime** means any part of the meeting lies outside 09:00–16:00 (`start < 09:00` or `end > 16:00`).
  A meeting ending exactly at 16:00 is prime.
- **Quota rounding** uses $\lceil r\,n \rceil$ with protection against floating-point noise. A department
  with one large component therefore needs that one component to be non-prime.
- **Hard vs soft** comes from the request's `hard` column, never from its type name.
- **FixedSchedule rows** become hard `UNAVAILABLE` requests for every member of the target group, so they
  shrink $P_c$ before optimization.
- **Overlapping conflict groups stack.** If a pair belongs to several groups, each group has its own
  $o_{jcc'}$ and its own penalty, exactly as the objective's double sum states.
- **Variables are created only where they matter.** $y_v$ exists only if $B_v \neq \emptyset$, and
  $o_{jcc'}$ only if $\Omega_{cc'} \neq \emptyset$.
- **A single instructor/parent pair** can appear in both C2 and C3, for example a lab taught by its
  lecturer. The duplicate rows are kept to stay faithful to the formulation; Gurobi's presolve removes them.

## Size (W27)

64 components, 231 generated patterns, 2,036 assignment variables, 4 soft cases, 117 group pairs.
In total about 2,160 binary variables and 17,900 constraints, which exceeds Gurobi's size-limited pip
license. The 7-component validation dataset has 187 variables and 223 constraints.
