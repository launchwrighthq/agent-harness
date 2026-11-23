# Feature specification

Date: YYYY-MM-DD
Status: draft

State the concrete problem in short sentences. Then list what this spec delivers:

1. **Deliverable.** One or two sentences of operator-visible behavior.
2. **Deliverable.** One or two sentences of operator-visible behavior.

## 1. Scope and current behavior

### 1.1 What the user asked

> The words of the user that started this spec, quoted as they are, with the date.

### 1.2 Acting accounts

Name the acting account, platform or consumer, and the authorization that live cases need.

### 1.3 Current implementation (local `<base branch>` `<short sha>`)

| Area | Implemented | Gap |
|---|---|---|
| Area name | What the code does today, with module or table names. | What is missing. |

### 1.4 Related specs

Link related specs. When this spec changes an earlier decision, name the exact row:

| Earlier row | Change in this spec |
|---|---|
| Other spec D4 | The new behavior. |

### 1.5 Exclusions

- One excluded behavior per bullet.

## 2. Proposed behavior

### 2.1 Decisions

| ID | Decision | Why | Rejected |
|---|---|---|---|
| D1 | One decision in two to four short sentences. Point to the subsection (2.2). | The reason. | The option it rejects, and why. |

### 2.2 First topic (D1)

One subsection per topic: inputs, outputs, persistence, submission intent, reconciliation, failure
and recovery, operator controls. Use a table for a set of cases. Separate proposed behavior from
measured results, and give each measurement its date.

## 3. Delivery and validation

### 3.1 Migration and configuration

Numbered steps. Say what a migration never does.

### 3.2 Documentation

Architecture documents, environment examples and operator help that change.

### 3.3 Synthetic checks

| Case | Required observation |
|---|---|
| Case name | The observable result that separates success from failure. |

Name the checks: the gate command of the repository (`ah config get harness.gateCommand`) for
application changes, and `ah spec render <path>` for this document.

The spec names the code. The code never names the spec: no row ID, spec section or date in a code
comment. A gap that this spec keeps open gets one marker line in the code,
`lw-gap <ID>: <removal condition>`, and its row names that condition.

### 3.4 Live qualification

- **L1. Case name.** The bounded run, its account, and the evidence it records. A live case is not
  permission to use real accounts.

### 3.5 Risks

- **Risk name.** What can go wrong and what limits it.

### 3.6 Open questions

| ID | Question | Default until answered |
|---|---|---|
| Q1 | One question for the user. | What the build does until the user answers. |

## 4. Amendments

| ID | Amendment (user, YYYY-MM-DD) |
|---|---|
| A1 | The user's scope decision, in plain words. Quote the user only when the exact words matter. |

## 5. Done means

| ID | Requirement | How to check it | Level |
|---|---|---|---|
| R1 | One observable outcome. | Evidence that separates success from failure. | built |
