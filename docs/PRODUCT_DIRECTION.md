# Product direction

## The promise

**Turn a question about a place into a defensible brief someone can understand,
check and reuse.**

Not a dashboard, not a variable browser, not a map toy. The unit of output is a
brief: one question, one place, one period, with the definitions, the
uncertainty and the sources attached, in a form a colleague can read and a
reviewer can check.

## What is hypothesis and what is fact

Everything in this section is a **hypothesis**. None of it has been validated
with a real customer.

| Claim | Status |
| --- | --- |
| Analysts at small civic nonprofits and planning consultancies are the initial audience | Hypothesis. No interviews conducted. |
| Journalists are a secondary audience | Hypothesis. |
| These people prepare recurring community reports and find the definitional work costly | Hypothesis. |
| Anyone would pay for this, or how much | **Entirely unvalidated.** No pricing work, no willingness-to-pay evidence, no commitments. |
| The end-to-end workflow is the differentiator | Hypothesis, argued below, untested. |

Validating this means watching real people complete a recurring report task and
seeing where they stall, not building more of it. Until that happens, the
roadmap below is a set of guesses ranked by our own judgement.

## The competitive position, honestly

Social Explorer has AI-assisted search, maps and reports. Census Reporter has
place profiles and comparisons. PolicyMap has reports and dashboards. All three
are established, and all three do things this build does not.

**We should not claim to be unique for having maps, profiles or comparisons.**
Having those features is table stakes, and saying otherwise would be the same
kind of overstatement this project exists to avoid.

The opportunity, if there is one, is narrower:

1. **An excellent end-to-end workflow.** From a question to a finished,
   shareable brief without leaving the tool and without learning a variable
   code. Most tools make you assemble the brief yourself somewhere else.
2. **Explicit definitions, in the output.** Every number arrives with what it
   counts, out of what, over what period, with what margin of error, from which
   published table. The denominator is part of the measure, not a toggle.
3. **Portable evidence.** The export is a bundle a reviewer can check: the
   data, the figure, the brief, and a provenance record naming every input file
   and its digest. A saved brief refuses to reopen if those inputs changed.
4. **Refusing rather than guessing.** Where a correct answer needs evidence we
   do not have — comparing across boundary vintages, for instance — the tool
   says so and says what would be needed. That is a feature for someone whose
   name goes on the report.

Differentiated historical exploration is a *later* possibility, and only if the
source work in `RESEARCH_PLAN.md` actually pans out. It is not a current claim.

## This milestone: the guided place brief

Scope, as delivered:

- **Three starting questions**, each mapped deterministically to measures the
  built catalog already carries: *Who lives here?*, *How do these places
  compare?*, *Where is this birthplace group concentrated?* A question offers
  nothing the dataset cannot draw, and says so when it cannot run at all.
- **A plain-language selection summary** before any number: the question, the
  places, the period, what is counted and out of what. Counts, share of
  residents and share of the foreign-born are offered as distinct measures,
  never as a display toggle.
- **A compatible benchmark**, built by adding the underlying counts and
  recomputing the measure from the totals — never by averaging percentages.
  Where a correct combined margin of error is not available, it says so.
- **Quality as three separate statements** — uncertainty, comparison
  eligibility, reference period — with no combined score and no claim of
  statistical significance or causation.
- **A print-ready brief** built from the same validated selection as the map,
  the table and the CSV, with the analyst's own commentary visibly marked as
  written rather than computed.
- **Saved briefs** that pin their inputs and refuse to reopen if those inputs
  changed.

Deliberately out of scope this pass: any new provider, historical bulk
extracts, analytics or tracking, paid services, accounts, billing, public
deployment, and any new dependency.

## Things we will not do to make it look better

- No AI answer layer over the data. Retrieval and arithmetic stay
  deterministic. A question is a route into validated measures, not an
  interpreter.
- No neighbourhood names without documented boundaries. A census tract is
  called a census tract.
- No feature advertised that the data does not support. This build has no
  income, poverty, housing or rent tables, so it offers none and says so.
- No single trust score. Three different problems deserve three statements.
- No "significant change" language without a test we actually perform.
- No testimonials, no decorative metric cards, no marketing page.

## Roadmap, as candidates rather than commitments

Ordered by our guess at value, all pending validation:

1. **Durable versioned briefs.** Today a saved brief detects that its inputs
   changed; it cannot restore what they were. Keeping the values themselves
   would make a brief reopenable years later. This is a real gap and the
   honest framing today is "tamper check, not archive".
2. **Explicit updates between releases.** When a newer release arrives, show
   what moved and what the change means — which needs the boundary-equivalence
   work to land first.
3. **Team review and templates.** A house style for briefs, a second pair of
   eyes before a brief leaves the building, reusable question sets.
4. **Source-validated historical exploration.** The eight research directions
   in `RESEARCH_PLAN.md`, each behind its own evidence gate. This is the only
   genuinely differentiated territory, and it is also the hardest.

## How we would know any of this is right

Not by building more of it. By watching several analysts do a report they
already have to do, on a deadline they already have, and counting where they
stop, what they paste into another tool, and what they have to look up
elsewhere. Until that has happened, this document is a plan, not a finding.
