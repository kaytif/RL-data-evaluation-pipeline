# Synthetic live demo

Each CSV is one task. Rows sharing an attempt ID belong to the same attempt.

| File | Attempt | Expected proposed score | Why |
|---|---|---|---|
| 01_filter_transactions.csv | csv-attempt-1 | 1 | Settled IDs and row count both match |
| 01_filter_transactions.csv | csv-attempt-2 | 0 | Includes pending transaction t2; wrong row count |
| 02_transform_customer.csv | json-attempt-1 | 1 | Uppercase name matches supplied reference |
| 02_transform_customer.csv | json-attempt-2 | 0 | Name remains lowercase |
| 03_extract_invoice.csv | text-attempt-1 | unverified | Source explicitly says the check was not completed |
| 03_extract_invoice.csv | text-attempt-2 | unverified | No expected supplier reference is supplied |

These are fabricated records, not actual model executions. The local agent checks their recorded evidence. This example does not validate a model, execute a vendor grader, or demonstrate training improvement. Human approval is left unset.
