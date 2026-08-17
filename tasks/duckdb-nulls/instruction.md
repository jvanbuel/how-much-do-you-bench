Two numbers in the monthly report are wrong, and one of them is wrong in a way
that looks like good news.

`bash /app/run_report.sh` runs `/app/report.sql` against `/app/shop.duckdb` and
writes two result sets into `/app/out`.

**Revenue by month.** Finance says several months show roughly double the revenue
they booked, and that the report has fewer rows than the business has been
trading months. The orders run from June 2025 to late 2026.

**Dormant customers.** This is meant to list customers with no order in the last
90 days, so sales can call them. It comes back empty, and sales have concluded
that every single customer is active. Roughly three hundred of them are not.

Fix both. Keep the two section markers in `report.sql` and the two output names.

Constraints:

- Do not edit `/app/shop.duckdb` or `/app/generate_data.py`.
- Do not change how `run_report.sh` finds and runs the sections.
- Report every dormant customer, including those whose country is unknown.
