# Example Input: Transaction Batch Mapping Specification

This is a sample specification document (pipe-separated). In practice, this would come from an Excel, PDF, or Word document.

```
Transformation|Column|Definition|Data Type|Position|Format|Length|Required|Valid Values|Notes
Default to '00040' for accounts charged-off yesterday|BANK-CODE|Bank identifier code|Numeric|1|9(5)|5|Y|Bank Control Table|
Default to '001'|APPL-CODE|Application identifier|Numeric|6|9(3)|3|Y|Application Control Table|
BRANCH + CUST + LOAN|ACCT-KEY|Account key (composite)|String|9||18|Y||
Nullable --> Leave Blank|REF-CODE|Reference code|String|27||3|N/A||Initialize to spaces
Nullable --> Leave Blank|FILLER-01|Filler for key alignment|String|30||130|N/A||Initialize to spaces
|TXN-DATE|Transaction effective date|Date|160|MM/DD/CCYY|10|Y||
Default to '32010'|TXN-TYPE|Transaction type code|Numeric|170|9(5)|5|Y|32010|
if 1st account in batch then '1'; if 2nd then '2'; if nth then 'n'|BATCH-SEQ|Sequential batch item number|Numeric|175|9(9)|9|Y||
Nullable --> Leave Blank|SRC-CODE|Source system code|Numeric|184|9(3)|3|N/A||Initialize to spaces
|TXN-COUNT|Transaction count per group|Numeric|187|9(4)|4|Y||
Default to '1'|MORE-DATA-FLAG|Continuation flag|String|191||1|Y||
```
