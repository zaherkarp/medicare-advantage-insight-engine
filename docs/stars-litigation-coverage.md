# Stars litigation source coverage

The source configuration uses independent discovery routes:

- Georgetown's Stars topic feed supplies curated cases and guaranteed topic context.
- CourtListener Stars filing and opinion searches use Star Ratings / quality-bonus language with Medicare or CMS. Neither requires False Claims Act language.
- A CourtListener insurer/HHS case-name search catches captions without Stars terminology. This is a broader review queue: an insurer suing HHS is not necessarily a Stars case.

The CourtListener feeds use plain RSS. They must not inject guaranteed Stars context into potentially unrelated results. Existing FCA searches remain separate.

## What this change does not establish

CourtListener/RECAP coverage depends on indexing and contributed filings. Georgetown can lag a new docket. Successful HTTP retrieval, an empty feed, or a passing offline test does not establish that there were no new cases. Search queries are discovery aids, not an exhaustive docket registry.

For the owner-reported misses, reconcile the BCBS Rhode Island CourtListener docket (75063767) and the Mount Carmel MediGold case against production fetch outcomes, stored stories, scores, duplicate grouping, and public visibility. Match cases by court and docket number when assessing coverage; distinct plaintiffs must not be assumed to be the same event merely because they challenge similar methodology.

This configuration change does not implement case-level reconciliation or change duplicate grouping. It does not backfill missing cases. Those require a separate production archive inspection and, if necessary, pipeline changes. Keep discovery completeness separate from whether an individual case merits a post.

Query syntax reference: https://www.courtlistener.com/help/search-operators/
