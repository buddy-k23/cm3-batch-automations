"""Write the BSA-reviewed TRANERT rules CSVs for COD and CBRS.

Applies all corrections from the BSA review (2026-05-25):
- COD: LGL-STA-COD-COD output codes only; RPO-COD-COD 0|1 only;
  ORG-LVL-NUM-4-COD 0000304|0000102 only; DUE-DAT-DAY-COD drop valid_values;
  DAT-LAS-RPO-COD drop valid_values; renumber sequentially.
- CBRS: drop all 24-CYC-DLQ-* valid_values; drop ACT-TYP-CBRS valid_values;
  drop LAS-DAT-RPT-CBRS valid_values; drop PET-DAT-CBRS valid_values;
  renumber sequentially.
"""

import pathlib

COD = pathlib.Path("mappings/csv/shaw_tranert/SHAW_TRANERT_COD_rules.csv")
CBRS = pathlib.Path("mappings/csv/shaw_tranert/SHAW_TRANERT_CBRS_rules.csv")
CUS = pathlib.Path("mappings/csv/shaw_tranert/SHAW_TRANERT_CUS_rules.csv")
ORI = pathlib.Path("mappings/csv/shaw_tranert/SHAW_TRANERT_ORI_rules.csv")

COD_ROWS = """Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
R001,BK-NUM-ERT required,BK-NUM-ERT,not_empty,error,Yes,BK-NUM-ERT must not be empty,
R002,BK-NUM-ERT numeric,BK-NUM-ERT,numeric,error,Yes,BK-NUM-ERT must be numeric,
R003,BK-NUM-ERT length check,BK-NUM-ERT,exact_length,error,Yes,BK-NUM-ERT must be exactly 5 characters,5
R004,BK-NUM-ERT valid values,BK-NUM-ERT,valid_values,error,Yes,BK-NUM-ERT must be one of: 00040,00040
R005,APP-ERT required,APP-ERT,not_empty,error,Yes,APP-ERT must not be empty,
R006,APP-ERT numeric,APP-ERT,numeric,error,Yes,APP-ERT must be numeric,
R007,APP-ERT length check,APP-ERT,exact_length,error,Yes,APP-ERT must be exactly 3 characters,3
R008,APP-ERT valid values,APP-ERT,valid_values,error,Yes,APP-ERT must be one of: 001,001
R009,LN-NUM-ERT required,LN-NUM-ERT,not_empty,error,Yes,LN-NUM-ERT must not be empty,
R010,LN-NUM-ERT length check,LN-NUM-ERT,length,error,Yes,LN-NUM-ERT payload length must be between 14 and 18 characters,14..18
R011,EFF-DAT-ERT required,EFF-DAT-ERT,not_empty,error,Yes,EFF-DAT-ERT must not be empty,
R012,EFF-DAT-ERT date format,EFF-DAT-ERT,date_format,error,Yes,EFF-DAT-ERT must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R013,EFF-DAT-ERT length check,EFF-DAT-ERT,exact_length,error,Yes,EFF-DAT-ERT must be exactly 10 characters,10
R014,TRN-COD-ERT required,TRN-COD-ERT,not_empty,error,Yes,TRN-COD-ERT must not be empty,
R015,TRN-COD-ERT numeric,TRN-COD-ERT,numeric,error,Yes,TRN-COD-ERT must be numeric,
R016,TRN-COD-ERT length check,TRN-COD-ERT,exact_length,error,Yes,TRN-COD-ERT must be exactly 5 characters,5
R017,TRN-COD-ERT valid values,TRN-COD-ERT,valid_values,error,Yes,TRN-COD-ERT must be one of: 32025,32025
R018,BAT-ITM-NUM-ERT required,BAT-ITM-NUM-ERT,not_empty,error,Yes,BAT-ITM-NUM-ERT must not be empty,
R019,BAT-ITM-NUM-ERT numeric,BAT-ITM-NUM-ERT,numeric,error,Yes,BAT-ITM-NUM-ERT must be numeric,
R020,BAT-ITM-NUM-ERT length check,BAT-ITM-NUM-ERT,exact_length,error,Yes,BAT-ITM-NUM-ERT must be exactly 9 characters,9
R021,INP-SRC-COD-ERT numeric,INP-SRC-COD-ERT,numeric,error,Yes,INP-SRC-COD-ERT must be numeric,
R022,TRN-CNT-ERT numeric,TRN-CNT-ERT,numeric,error,Yes,TRN-CNT-ERT must be numeric,
R023,STP-ACR-ATY-COD-COD numeric,STP-ACR-ATY-COD-COD,numeric,error,Yes,STP-ACR-ATY-COD-COD must be numeric,
R024,DAT-PLC-IN-STP-COD date format,DAT-PLC-IN-STP-COD,date_format,error,Yes,DAT-PLC-IN-STP-COD must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R025,DAT-STP-ACR-RMD-COD date format,DAT-STP-ACR-RMD-COD,date_format,error,Yes,DAT-STP-ACR-RMD-COD must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R026,INT-ACR-WHI-STP-COD numeric,INT-ACR-WHI-STP-COD,numeric,error,Yes,INT-ACR-WHI-STP-COD must be numeric,
R027,LGL-STA-COD-COD valid values,LGL-STA-COD-COD,valid_values,error,Yes,LGL-STA-COD-COD must be one of: STL|RPO|B06|FCL|PRP|TBD,STL|RPO|B06|FCL|PRP|TBD
R028,DAT-LAS-RPO-COD date format,DAT-LAS-RPO-COD,date_format,error,Yes,DAT-LAS-RPO-COD must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R029,RPO-COD-COD numeric,RPO-COD-COD,numeric,error,Yes,RPO-COD-COD must be numeric,
R030,RPO-COD-COD valid values,RPO-COD-COD,valid_values,error,Yes,RPO-COD-COD must be one of: 0|1,0|1
R031,DUE-DAT-DAY-COD required,DUE-DAT-DAY-COD,not_empty,error,Yes,DUE-DAT-DAY-COD must not be empty,
R032,DUE-DAT-DAY-COD numeric,DUE-DAT-DAY-COD,numeric,error,Yes,DUE-DAT-DAY-COD must be numeric,
R033,DUE-DAT-DAY-COD length check,DUE-DAT-DAY-COD,exact_length,error,Yes,DUE-DAT-DAY-COD must be exactly 2 characters,2
R034,DAT-RMD-RPO-COD date format,DAT-RMD-RPO-COD,date_format,error,Yes,DAT-RMD-RPO-COD must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R035,ORG-LVL-NUM-1-COD numeric,ORG-LVL-NUM-1-COD,numeric,error,Yes,ORG-LVL-NUM-1-COD must be numeric,
R036,ORG-LVL-NUM-1-COD valid values,ORG-LVL-NUM-1-COD,valid_values,error,Yes,ORG-LVL-NUM-1-COD must be one of: 0000001,0000001
R037,ORG-LVL-NUM-2-COD numeric,ORG-LVL-NUM-2-COD,numeric,error,Yes,ORG-LVL-NUM-2-COD must be numeric,
R038,ORG-LVL-NUM-2-COD valid values,ORG-LVL-NUM-2-COD,valid_values,error,Yes,ORG-LVL-NUM-2-COD must be one of: 0000001,0000001
R039,ORG-LVL-NUM-3-COD numeric,ORG-LVL-NUM-3-COD,numeric,error,Yes,ORG-LVL-NUM-3-COD must be numeric,
R040,ORG-LVL-NUM-3-COD valid values,ORG-LVL-NUM-3-COD,valid_values,error,Yes,ORG-LVL-NUM-3-COD must be one of: 0000040,0000040
R041,ORG-LVL-NUM-4-COD numeric,ORG-LVL-NUM-4-COD,numeric,error,Yes,ORG-LVL-NUM-4-COD must be numeric,
R042,ORG-LVL-NUM-4-COD valid values,ORG-LVL-NUM-4-COD,valid_values,error,Yes,ORG-LVL-NUM-4-COD must be one of: 0000304|0000102,0000304|0000102
R043,ORG-LVL-NUM-5-COD numeric,ORG-LVL-NUM-5-COD,numeric,error,Yes,ORG-LVL-NUM-5-COD must be numeric,
R044,ORG-LVL-NUM-5-COD valid values,ORG-LVL-NUM-5-COD,valid_values,error,Yes,ORG-LVL-NUM-5-COD must be one of: 0000001,0000001
R045,ORG-LVL-NUM-6-COD numeric,ORG-LVL-NUM-6-COD,numeric,error,Yes,ORG-LVL-NUM-6-COD must be numeric,
R046,ORG-LVL-NUM-7-COD numeric,ORG-LVL-NUM-7-COD,numeric,error,Yes,ORG-LVL-NUM-7-COD must be numeric,
R047,ORG-LVL-NUM-8-COD numeric,ORG-LVL-NUM-8-COD,numeric,error,Yes,ORG-LVL-NUM-8-COD must be numeric,
R048,ORG-LVL-NUM-9-COD numeric,ORG-LVL-NUM-9-COD,numeric,error,Yes,ORG-LVL-NUM-9-COD must be numeric,
R049,ORG-LVL-NUM-10-COD numeric,ORG-LVL-NUM-10-COD,numeric,error,Yes,ORG-LVL-NUM-10-COD must be numeric,
R050,ORG-LVL-NUM-11-COD numeric,ORG-LVL-NUM-11-COD,numeric,error,Yes,ORG-LVL-NUM-11-COD must be numeric,
R051,ORG-LVL-NUM-12-COD numeric,ORG-LVL-NUM-12-COD,numeric,error,Yes,ORG-LVL-NUM-12-COD must be numeric,
R052,OGL-LN-POO-NUM-COD numeric,OGL-LN-POO-NUM-COD,numeric,error,Yes,OGL-LN-POO-NUM-COD must be numeric,
R053,OGL-BR-NUM-COD numeric,OGL-BR-NUM-COD,numeric,error,Yes,OGL-BR-NUM-COD must be numeric,
R054,LN-PUR-COD-COD valid values,LN-PUR-COD-COD,valid_values,error,Yes,LN-PUR-COD-COD must be one of: 3,3
R055,LN-CAT-COD required,LN-CAT-COD,not_empty,error,Yes,LN-CAT-COD must not be empty,
R056,LN-CAT-COD numeric,LN-CAT-COD,numeric,error,Yes,LN-CAT-COD must be numeric,
R057,LN-CAT-COD length check,LN-CAT-COD,exact_length,error,Yes,LN-CAT-COD must be exactly 3 characters,3
R058,DLR-NUM-COD numeric,DLR-NUM-COD,numeric,error,Yes,DLR-NUM-COD must be numeric,
R059,FCL-EFF-DAT-COD date format,FCL-EFF-DAT-COD,date_format,error,Yes,FCL-EFF-DAT-COD must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R060,STM-FRQ-COD required,STM-FRQ-COD,not_empty,error,Yes,STM-FRQ-COD must not be empty,
R061,STM-FRQ-COD length check,STM-FRQ-COD,length,error,Yes,STM-FRQ-COD payload length must be between 1 and 1 characters,1..1
R062,STM-FRQ-COD valid values,STM-FRQ-COD,valid_values,error,Yes,STM-FRQ-COD must be one of: M,M
R063,NATL-CURRENCY-COD required,NATL-CURRENCY-COD,not_empty,error,Yes,NATL-CURRENCY-COD must not be empty,
R064,NATL-CURRENCY-COD length check,NATL-CURRENCY-COD,length,error,Yes,NATL-CURRENCY-COD payload length must be between 1 and 3 characters,1..3
R065,NATL-CURRENCY-COD valid values,NATL-CURRENCY-COD,valid_values,error,Yes,NATL-CURRENCY-COD must be one of: USD,USD
R066,PREF-CURRENCY-COD required,PREF-CURRENCY-COD,not_empty,error,Yes,PREF-CURRENCY-COD must not be empty,
R067,PREF-CURRENCY-COD length check,PREF-CURRENCY-COD,length,error,Yes,PREF-CURRENCY-COD payload length must be between 1 and 3 characters,1..3
R068,PREF-CURRENCY-COD valid values,PREF-CURRENCY-COD,valid_values,error,Yes,PREF-CURRENCY-COD must be one of: USD,USD
R069,BASE-CURRENCY-COD required,BASE-CURRENCY-COD,not_empty,error,Yes,BASE-CURRENCY-COD must not be empty,
R070,BASE-CURRENCY-COD length check,BASE-CURRENCY-COD,length,error,Yes,BASE-CURRENCY-COD payload length must be between 1 and 3 characters,1..3
R071,BASE-CURRENCY-COD valid values,BASE-CURRENCY-COD,valid_values,error,Yes,BASE-CURRENCY-COD must be one of: USD,USD
R072,PRT-COF-BK-NUM-COD numeric,PRT-COF-BK-NUM-COD,numeric,error,Yes,PRT-COF-BK-NUM-COD must be numeric,
R073,PRT-COF-APP-COD numeric,PRT-COF-APP-COD,numeric,error,Yes,PRT-COF-APP-COD must be numeric,
R074,NAS-SRC-COD-COD numeric,NAS-SRC-COD-COD,numeric,error,Yes,NAS-SRC-COD-COD must be numeric,
R075,NAS-SRC-COD-COD valid values,NAS-SRC-COD-COD,valid_values,error,Yes,NAS-SRC-COD-COD must be one of: 130,130
R076,IRS-NUM-OF-MORT-COD numeric,IRS-NUM-OF-MORT-COD,numeric,error,Yes,IRS-NUM-OF-MORT-COD must be numeric,
R077,IRS-NUM-OF-MORT-COD valid values,IRS-NUM-OF-MORT-COD,valid_values,error,Yes,IRS-NUM-OF-MORT-COD must be one of: 0000,0000
"""

CBRS_ROWS = """Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
R001,BK-NUM-ERT required,BK-NUM-ERT,not_empty,error,Yes,BK-NUM-ERT must not be empty,
R002,BK-NUM-ERT numeric,BK-NUM-ERT,numeric,error,Yes,BK-NUM-ERT must be numeric,
R003,BK-NUM-ERT length check,BK-NUM-ERT,exact_length,error,Yes,BK-NUM-ERT must be exactly 5 characters,5
R004,BK-NUM-ERT valid values,BK-NUM-ERT,valid_values,error,Yes,BK-NUM-ERT must be one of: 00040,00040
R005,APP-ERT required,APP-ERT,not_empty,error,Yes,APP-ERT must not be empty,
R006,APP-ERT numeric,APP-ERT,numeric,error,Yes,APP-ERT must be numeric,
R007,APP-ERT length check,APP-ERT,exact_length,error,Yes,APP-ERT must be exactly 3 characters,3
R008,APP-ERT valid values,APP-ERT,valid_values,error,Yes,APP-ERT must be one of: 001,001
R009,LN-NUM-ERT required,LN-NUM-ERT,not_empty,error,Yes,LN-NUM-ERT must not be empty,
R010,LN-NUM-ERT length check,LN-NUM-ERT,length,error,Yes,LN-NUM-ERT payload length must be between 14 and 18 characters,14..18
R011,EFF-DAT-ERT required,EFF-DAT-ERT,not_empty,error,Yes,EFF-DAT-ERT must not be empty,
R012,EFF-DAT-ERT date format,EFF-DAT-ERT,date_format,error,Yes,EFF-DAT-ERT must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R013,EFF-DAT-ERT length check,EFF-DAT-ERT,exact_length,error,Yes,EFF-DAT-ERT must be exactly 10 characters,10
R014,TRN-COD-ERT required,TRN-COD-ERT,not_empty,error,Yes,TRN-COD-ERT must not be empty,
R015,TRN-COD-ERT numeric,TRN-COD-ERT,numeric,error,Yes,TRN-COD-ERT must be numeric,
R016,TRN-COD-ERT length check,TRN-COD-ERT,exact_length,error,Yes,TRN-COD-ERT must be exactly 5 characters,5
R017,TRN-COD-ERT valid values,TRN-COD-ERT,valid_values,error,Yes,TRN-COD-ERT must be one of: 32040,32040
R018,BAT-ITM-NUM-ERT required,BAT-ITM-NUM-ERT,not_empty,error,Yes,BAT-ITM-NUM-ERT must not be empty,
R019,BAT-ITM-NUM-ERT numeric,BAT-ITM-NUM-ERT,numeric,error,Yes,BAT-ITM-NUM-ERT must be numeric,
R020,BAT-ITM-NUM-ERT length check,BAT-ITM-NUM-ERT,exact_length,error,Yes,BAT-ITM-NUM-ERT must be exactly 9 characters,9
R021,INP-SRC-COD-ERT numeric,INP-SRC-COD-ERT,numeric,error,Yes,INP-SRC-COD-ERT must be numeric,
R022,TRN-CNT-ERT numeric,TRN-CNT-ERT,numeric,error,Yes,TRN-CNT-ERT must be numeric,
R023,DAT-DLQ-STR-CBRS date format,DAT-DLQ-STR-CBRS,date_format,error,Yes,DAT-DLQ-STR-CBRS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R024,M2F-CMT-COD-CBRS valid values,M2F-CMT-COD-CBRS,valid_values,error,Yes,M2F-CMT-COD-CBRS must be one of: AW|AU,AW|AU
R025,HGH-DAY-DLQ-CBRS numeric,HGH-DAY-DLQ-CBRS,numeric,error,Yes,HGH-DAY-DLQ-CBRS must be numeric,
R026,HGH-AMT-DLQ-CBRS numeric,HGH-AMT-DLQ-CBRS,numeric,error,Yes,HGH-AMT-DLQ-CBRS must be numeric,
R027,LAS-LN-BAL-CBRS numeric,LAS-LN-BAL-CBRS,numeric,error,Yes,LAS-LN-BAL-CBRS must be numeric,
R028,LAS-DAT-RPT-CBRS date format,LAS-DAT-RPT-CBRS,date_format,error,Yes,LAS-DAT-RPT-CBRS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R029,LAS-ACT-STA-CBRS valid values,LAS-ACT-STA-CBRS,valid_values,error,Yes,LAS-ACT-STA-CBRS must be one of: 05|63|64|96|97|DA|DF,05|63|64|96|97|DA|DF
R030,LAS-CMT-COD-CBRS valid values,LAS-CMT-COD-CBRS,valid_values,error,Yes,LAS-CMT-COD-CBRS must be one of: AU|AW|ST,AU|AW|ST
R031,CUR-PMT-RTG-CBRS valid values,CUR-PMT-RTG-CBRS,valid_values,error,Yes,CUR-PMT-RTG-CBRS must be one of: L,L
R032,PET-DAT-CBRS date format,PET-DAT-CBRS,date_format,error,Yes,PET-DAT-CBRS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R033,PET-SCH-PMT-AMT-CBRS numeric,PET-SCH-PMT-AMT-CBRS,numeric,error,Yes,PET-SCH-PMT-AMT-CBRS must be numeric,
R034,PET-CUR-BAL-CBRS numeric,PET-CUR-BAL-CBRS,numeric,error,Yes,PET-CUR-BAL-CBRS must be numeric,
R035,PET-AMT-PAS-DUE-CBRS numeric,PET-AMT-PAS-DUE-CBRS,numeric,error,Yes,PET-AMT-PAS-DUE-CBRS must be numeric,
R036,CHX-LAS-DAT-RPT-CBRS date format,CHX-LAS-DAT-RPT-CBRS,date_format,error,Yes,CHX-LAS-DAT-RPT-CBRS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R037,SEC-LAS-DAT-RPT-CBRS date format,SEC-LAS-DAT-RPT-CBRS,date_format,error,Yes,SEC-LAS-DAT-RPT-CBRS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R038,SEC-LAS-LN-BAL-CBRS numeric,SEC-LAS-LN-BAL-CBRS,numeric,error,Yes,SEC-LAS-LN-BAL-CBRS must be numeric,
"""

COD.write_text(COD_ROWS.lstrip(), encoding="utf-8", newline="")
CBRS.write_text(CBRS_ROWS.lstrip(), encoding="utf-8", newline="")
print("COD done:", COD.stat().st_size, "bytes")
print("CBRS done:", CBRS.stat().st_size, "bytes")

CUS_ROWS = """Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
R001,BK-NUM-ERT required,BK-NUM-ERT,not_empty,error,Yes,BK-NUM-ERT must not be empty,
R002,BK-NUM-ERT numeric,BK-NUM-ERT,numeric,error,Yes,BK-NUM-ERT must be numeric,
R003,BK-NUM-ERT length check,BK-NUM-ERT,exact_length,error,Yes,BK-NUM-ERT must be exactly 5 characters,5
R004,APP-ERT required,APP-ERT,not_empty,error,Yes,APP-ERT must not be empty,
R005,APP-ERT numeric,APP-ERT,numeric,error,Yes,APP-ERT must be numeric,
R006,APP-ERT length check,APP-ERT,exact_length,error,Yes,APP-ERT must be exactly 3 characters,3
R007,LN-NUM-ERT required,LN-NUM-ERT,not_empty,error,Yes,LN-NUM-ERT must not be empty,
R008,LN-NUM-ERT length check,LN-NUM-ERT,length,error,Yes,LN-NUM-ERT payload length must be between 14 and 18 characters,14..18
R009,EFF-DAT-ERT required,EFF-DAT-ERT,not_empty,error,Yes,EFF-DAT-ERT must not be empty,
R010,EFF-DAT-ERT date format,EFF-DAT-ERT,date_format,error,Yes,EFF-DAT-ERT must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R011,EFF-DAT-ERT length check,EFF-DAT-ERT,exact_length,error,Yes,EFF-DAT-ERT must be exactly 10 characters,10
R012,TRN-COD-ERT required,TRN-COD-ERT,not_empty,error,Yes,TRN-COD-ERT must not be empty,
R013,TRN-COD-ERT numeric,TRN-COD-ERT,numeric,error,Yes,TRN-COD-ERT must be numeric,
R014,TRN-COD-ERT length check,TRN-COD-ERT,exact_length,error,Yes,TRN-COD-ERT must be exactly 5 characters,5
R015,TRN-COD-ERT valid values,TRN-COD-ERT,valid_values,error,Yes,TRN-COD-ERT must be one of: 32005,32005
R016,BAT-ITM-NUM-ERT required,BAT-ITM-NUM-ERT,not_empty,error,Yes,BAT-ITM-NUM-ERT must not be empty,
R017,BAT-ITM-NUM-ERT numeric,BAT-ITM-NUM-ERT,numeric,error,Yes,BAT-ITM-NUM-ERT must be numeric,
R018,BAT-ITM-NUM-ERT length check,BAT-ITM-NUM-ERT,exact_length,error,Yes,BAT-ITM-NUM-ERT must be exactly 9 characters,9
R019,INP-SRC-COD-ERT numeric,INP-SRC-COD-ERT,numeric,error,Yes,INP-SRC-COD-ERT must be numeric,
R020,TRN-CNT-ERT numeric,TRN-CNT-ERT,numeric,error,Yes,TRN-CNT-ERT must be numeric,
R021,CIF-ACT-NUM-CUS required,CIF-ACT-NUM-CUS,not_empty,error,Yes,CIF-ACT-NUM-CUS must not be empty,
R022,CIF-ACT-NUM-CUS length check,CIF-ACT-NUM-CUS,length,error,Yes,CIF-ACT-NUM-CUS payload length must be between 1 and 24 characters,1..24
R023,CIF-ACT-COD-CUS required,CIF-ACT-COD-CUS,not_empty,error,Yes,CIF-ACT-COD-CUS must not be empty,
R024,CIF-ACT-COD-CUS length check,CIF-ACT-COD-CUS,length,error,Yes,CIF-ACT-COD-CUS payload length must be between 1 and 1 characters,1..1
R025,CIF-CBR-RPT-IND-CUS valid values,CIF-CBR-RPT-IND-CUS,valid_values,error,Yes,CIF-CBR-RPT-IND-CUS must be one of: Y|N,Y|N
R026,DAT-BKY-REC-CUS date format,DAT-BKY-REC-CUS,date_format,error,Yes,DAT-BKY-REC-CUS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R027,CIF-REF-NUM-CUS required,CIF-REF-NUM-CUS,not_empty,error,Yes,CIF-REF-NUM-CUS must not be empty,
R028,CIF-REF-NUM-CUS length check,CIF-REF-NUM-CUS,length,error,Yes,CIF-REF-NUM-CUS payload length must be between 1 and 3 characters,1..3
R029,ECOA-CODE-CUS valid values,ECOA-CODE-CUS,valid_values,error,Yes,ECOA-CODE-CUS must be one of: 1|2|3|4|5|6|7|T|W|X|Z,1|2|3|4|5|6|7|T|W|X|Z
R030,LAST-ECOA-CODE-CUS valid values,LAST-ECOA-CODE-CUS,valid_values,error,Yes,LAST-ECOA-CODE-CUS must be one of: 1|2|3|4|5|6|7|T|W|X|Z,1|2|3|4|5|6|7|T|W|X|Z
R031,LAST-BUREAU-RECORDED-DATE-CUS date format,LAST-BUREAU-RECORDED-DATE-CUS,date_format,error,Yes,LAST-BUREAU-RECORDED-DATE-CUS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R032,FINAL-REPORT-INDICATOR-CUS valid values,FINAL-REPORT-INDICATOR-CUS,valid_values,error,Yes,FINAL-REPORT-INDICATOR-CUS must be one of: Y|N,Y|N
R033,LAST-REPORTED-SEG-TYPE-CUS valid values,LAST-REPORTED-SEG-TYPE-CUS,valid_values,error,Yes,LAST-REPORTED-SEG-TYPE-CUS must be one of: BA|J1,BA|J1
R034,LAST-CHEX-RECORDED-DATE-CUS date format,LAST-CHEX-RECORDED-DATE-CUS,date_format,error,Yes,LAST-CHEX-RECORDED-DATE-CUS must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R035,ACTION-CODE required,ACTION-CODE,not_empty,error,Yes,ACTION-CODE must not be empty,
R036,ACTION-CODE length check,ACTION-CODE,length,error,Yes,ACTION-CODE payload length must be between 1 and 1 characters,1..1
R037,CONTACT-ID required,CONTACT-ID,not_empty,error,Yes,CONTACT-ID must not be empty,
R038,CONTACT-ID length check,CONTACT-ID,length,error,Yes,CONTACT-ID payload length must be between 1 and 24 characters,1..24
R039,LOCATION-CODE required,LOCATION-CODE,not_empty,error,Yes,LOCATION-CODE must not be empty,
R040,LOCATION-CODE length check,LOCATION-CODE,length,error,Yes,LOCATION-CODE payload length must be between 1 and 6 characters,1..6
R041,ACCT-NUM required,ACCT-NUM,not_empty,error,Yes,ACCT-NUM must not be empty,
R042,ACCT-NUM length check,ACCT-NUM,length,error,Yes,ACCT-NUM payload length must be between 14 and 18 characters,14..18
R043,NAME-RELATIONSHIP required,NAME-RELATIONSHIP,not_empty,error,Yes,NAME-RELATIONSHIP must not be empty,
R044,NAME-RELATIONSHIP length check,NAME-RELATIONSHIP,length,error,Yes,NAME-RELATIONSHIP payload length must be between 1 and 1 characters,1..1
R045,LEAD-CONTACT-IND required,LEAD-CONTACT-IND,not_empty,error,Yes,LEAD-CONTACT-IND must not be empty,
R046,LEAD-CONTACT-IND length check,LEAD-CONTACT-IND,exact_length,error,Yes,LEAD-CONTACT-IND must be exactly 1 characters,1
R047,LEAD-CONTACT-IND valid values,LEAD-CONTACT-IND,valid_values,error,Yes,LEAD-CONTACT-IND must be one of: 0|1,0|1
R048,RESPONSIBLE-PARTY required,RESPONSIBLE-PARTY,not_empty,error,Yes,RESPONSIBLE-PARTY must not be empty,
R049,RESPONSIBLE-PARTY length check,RESPONSIBLE-PARTY,exact_length,error,Yes,RESPONSIBLE-PARTY must be exactly 1 characters,1
R050,RESPONSIBLE-PARTY valid values,RESPONSIBLE-PARTY,valid_values,error,Yes,RESPONSIBLE-PARTY must be one of: 0|1,0|1
R051,CAS-ADDRESS-IND required,CAS-ADDRESS-IND,not_empty,error,Yes,CAS-ADDRESS-IND must not be empty,
R052,CAS-ADDRESS-IND length check,CAS-ADDRESS-IND,length,error,Yes,CAS-ADDRESS-IND payload length must be between 1 and 1 characters,1..1
R053,EXTERNAL-SYSTEM-ID required,EXTERNAL-SYSTEM-ID,not_empty,error,Yes,EXTERNAL-SYSTEM-ID must not be empty,
R054,EXTERNAL-SYSTEM-ID length check,EXTERNAL-SYSTEM-ID,length,error,Yes,EXTERNAL-SYSTEM-ID payload length must be between 1 and 4 characters,1..4
R055,PREFERRED-CURRENCY required,PREFERRED-CURRENCY,not_empty,error,Yes,PREFERRED-CURRENCY must not be empty,
R056,PREFERRED-CURRENCY length check,PREFERRED-CURRENCY,length,error,Yes,PREFERRED-CURRENCY payload length must be between 1 and 3 characters,1..3
"""

ORI_ROWS = """Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values
R001,BK-NUM-ERT required,BK-NUM-ERT,not_empty,error,Yes,BK-NUM-ERT must not be empty,
R002,BK-NUM-ERT numeric,BK-NUM-ERT,numeric,error,Yes,BK-NUM-ERT must be numeric,
R003,BK-NUM-ERT length check,BK-NUM-ERT,exact_length,error,Yes,BK-NUM-ERT must be exactly 5 characters,5
R004,BK-NUM-ERT valid values,BK-NUM-ERT,valid_values,error,Yes,BK-NUM-ERT must be one of: 00040,00040
R005,APP-ERT required,APP-ERT,not_empty,error,Yes,APP-ERT must not be empty,
R006,APP-ERT numeric,APP-ERT,numeric,error,Yes,APP-ERT must be numeric,
R007,APP-ERT length check,APP-ERT,exact_length,error,Yes,APP-ERT must be exactly 3 characters,3
R008,APP-ERT valid values,APP-ERT,valid_values,error,Yes,APP-ERT must be one of: 001,001
R009,LN-NUM-ERT required,LN-NUM-ERT,not_empty,error,Yes,LN-NUM-ERT must not be empty,
R010,LN-NUM-ERT length check,LN-NUM-ERT,length,error,Yes,LN-NUM-ERT payload length must be between 14 and 18 characters,14..18
R011,EFF-DAT-ERT required,EFF-DAT-ERT,not_empty,error,Yes,EFF-DAT-ERT must not be empty,
R012,EFF-DAT-ERT date format,EFF-DAT-ERT,date_format,error,Yes,EFF-DAT-ERT must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R013,EFF-DAT-ERT length check,EFF-DAT-ERT,exact_length,error,Yes,EFF-DAT-ERT must be exactly 10 characters,10
R014,TRN-COD-ERT required,TRN-COD-ERT,not_empty,error,Yes,TRN-COD-ERT must not be empty,
R015,TRN-COD-ERT numeric,TRN-COD-ERT,numeric,error,Yes,TRN-COD-ERT must be numeric,
R016,TRN-COD-ERT length check,TRN-COD-ERT,exact_length,error,Yes,TRN-COD-ERT must be exactly 5 characters,5
R017,TRN-COD-ERT valid values,TRN-COD-ERT,valid_values,error,Yes,TRN-COD-ERT must be one of: 32010,32010
R018,BAT-ITM-NUM-ERT required,BAT-ITM-NUM-ERT,not_empty,error,Yes,BAT-ITM-NUM-ERT must not be empty,
R019,BAT-ITM-NUM-ERT numeric,BAT-ITM-NUM-ERT,numeric,error,Yes,BAT-ITM-NUM-ERT must be numeric,
R020,BAT-ITM-NUM-ERT length check,BAT-ITM-NUM-ERT,exact_length,error,Yes,BAT-ITM-NUM-ERT must be exactly 9 characters,9
R021,INP-SRC-COD-ERT numeric,INP-SRC-COD-ERT,numeric,error,Yes,INP-SRC-COD-ERT must be numeric,
R022,TRN-CNT-ERT numeric,TRN-CNT-ERT,numeric,error,Yes,TRN-CNT-ERT must be numeric,
R023,OGL-CONTRACT-DAT-ORI required,OGL-CONTRACT-DAT-ORI,not_empty,error,Yes,OGL-CONTRACT-DAT-ORI must not be empty,
R024,OGL-CONTRACT-DAT-ORI date format,OGL-CONTRACT-DAT-ORI,date_format,error,Yes,OGL-CONTRACT-DAT-ORI must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R025,OGL-CONTRACT-DAT-ORI length check,OGL-CONTRACT-DAT-ORI,exact_length,error,Yes,OGL-CONTRACT-DAT-ORI must be exactly 10 characters,10
R026,OGL-TRM-ORI numeric,OGL-TRM-ORI,numeric,error,Yes,OGL-TRM-ORI must be numeric,
R027,OGL-MAT-DAT-ORI date format,OGL-MAT-DAT-ORI,date_format,error,Yes,OGL-MAT-DAT-ORI must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R028,OGL-CONTRACT-AMT-ORI numeric,OGL-CONTRACT-AMT-ORI,numeric,error,Yes,OGL-CONTRACT-AMT-ORI must be numeric,
R029,OGL-PORTFOLIO-TYP-ORI required,OGL-PORTFOLIO-TYP-ORI,not_empty,error,Yes,OGL-PORTFOLIO-TYP-ORI must not be empty,
R030,OGL-PORTFOLIO-TYP-ORI length check,OGL-PORTFOLIO-TYP-ORI,length,error,Yes,OGL-PORTFOLIO-TYP-ORI payload length must be between 1 and 1 characters,1..1
R031,OGL-PORTFOLIO-TYP-ORI valid values,OGL-PORTFOLIO-TYP-ORI,valid_values,error,Yes,OGL-PORTFOLIO-TYP-ORI must be one of: C|I,C|I
R032,LN-TYP-ORI required,LN-TYP-ORI,not_empty,error,Yes,LN-TYP-ORI must not be empty,
R033,LN-TYP-ORI numeric,LN-TYP-ORI,numeric,error,Yes,LN-TYP-ORI must be numeric,
R034,LN-TYP-ORI length check,LN-TYP-ORI,exact_length,error,Yes,LN-TYP-ORI must be exactly 3 characters,3
R035,COF-CDN-IND-ORI numeric,COF-CDN-IND-ORI,numeric,error,Yes,COF-CDN-IND-ORI must be numeric,
R036,COF-CDN-IND-ORI valid values,COF-CDN-IND-ORI,valid_values,error,Yes,COF-CDN-IND-ORI must be one of: 1,1
R037,OGL-NTE-DAT-ORI required,OGL-NTE-DAT-ORI,not_empty,error,Yes,OGL-NTE-DAT-ORI must not be empty,
R038,OGL-NTE-DAT-ORI date format,OGL-NTE-DAT-ORI,date_format,error,Yes,OGL-NTE-DAT-ORI must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R039,OGL-NTE-DAT-ORI length check,OGL-NTE-DAT-ORI,exact_length,error,Yes,OGL-NTE-DAT-ORI must be exactly 10 characters,10
R040,OGL-NTE-AMT-ORI required,OGL-NTE-AMT-ORI,not_empty,error,Yes,OGL-NTE-AMT-ORI must not be empty,
R041,OGL-NTE-AMT-ORI numeric,OGL-NTE-AMT-ORI,numeric,error,Yes,OGL-NTE-AMT-ORI must be numeric,
R042,OGL-NTE-AMT-ORI length check,OGL-NTE-AMT-ORI,length,error,Yes,OGL-NTE-AMT-ORI payload length must be between 1 and 22 characters,1..22
R043,OGL-COF-INT-AMT-ORI numeric,OGL-COF-INT-AMT-ORI,numeric,error,Yes,OGL-COF-INT-AMT-ORI must be numeric,
R044,INT-RT-ORI numeric,INT-RT-ORI,numeric,error,Yes,INT-RT-ORI must be numeric,
R045,OGL-INT-RT-ORI numeric,OGL-INT-RT-ORI,numeric,error,Yes,OGL-INT-RT-ORI must be numeric,
R046,REP-TYP-ORI required,REP-TYP-ORI,not_empty,error,Yes,REP-TYP-ORI must not be empty,
R047,REP-TYP-ORI numeric,REP-TYP-ORI,numeric,error,Yes,REP-TYP-ORI must be numeric,
R048,REP-TYP-ORI length check,REP-TYP-ORI,exact_length,error,Yes,REP-TYP-ORI must be exactly 3 characters,3
R049,HGH-BAL-ORI numeric,HGH-BAL-ORI,numeric,error,Yes,HGH-BAL-ORI must be numeric,
R050,AMT-PAS-DUE-30-ORI numeric,AMT-PAS-DUE-30-ORI,numeric,error,Yes,AMT-PAS-DUE-30-ORI must be numeric,
R051,AMT-PAS-DUE-60-ORI numeric,AMT-PAS-DUE-60-ORI,numeric,error,Yes,AMT-PAS-DUE-60-ORI must be numeric,
R052,AMT-PAS-DUE-90-ORI numeric,AMT-PAS-DUE-90-ORI,numeric,error,Yes,AMT-PAS-DUE-90-ORI must be numeric,
R053,AMT-PAS-DUE-120-ORI numeric,AMT-PAS-DUE-120-ORI,numeric,error,Yes,AMT-PAS-DUE-120-ORI must be numeric,
R054,OGL-NUM-PMT-MTD-ORI numeric,OGL-NUM-PMT-MTD-ORI,numeric,error,Yes,OGL-NUM-PMT-MTD-ORI must be numeric,
R055,OGL-NUM-PMT-YTD-ORI numeric,OGL-NUM-PMT-YTD-ORI,numeric,error,Yes,OGL-NUM-PMT-YTD-ORI must be numeric,
R056,OGL-NUM-PMT-LTD-ORI numeric,OGL-NUM-PMT-LTD-ORI,numeric,error,Yes,OGL-NUM-PMT-LTD-ORI must be numeric,
R057,OGL-PMT-AMT-MTD-ORI numeric,OGL-PMT-AMT-MTD-ORI,numeric,error,Yes,OGL-PMT-AMT-MTD-ORI must be numeric,
R058,OGL-PMT-AMT-YTD-ORI numeric,OGL-PMT-AMT-YTD-ORI,numeric,error,Yes,OGL-PMT-AMT-YTD-ORI must be numeric,
R059,OGL-PMT-AMT-LTD-ORI numeric,OGL-PMT-AMT-LTD-ORI,numeric,error,Yes,OGL-PMT-AMT-LTD-ORI must be numeric,
R060,ST-COD-ORI valid values,ST-COD-ORI,valid_values,error,Yes,ST-COD-ORI must be one of: NC|SC|VA|GA|MD|DC|WV|KY|TX|NV|TN|AL|IN|FL|MS|AR|OH|PA|NJ,NC|SC|VA|GA|MD|DC|WV|KY|TX|NV|TN|AL|IN|FL|MS|AR|OH|PA|NJ
R061,DAT-INT-ACR-TO-ORI required,DAT-INT-ACR-TO-ORI,not_empty,error,Yes,DAT-INT-ACR-TO-ORI must not be empty,
R062,DAT-INT-ACR-TO-ORI date format,DAT-INT-ACR-TO-ORI,date_format,error,Yes,DAT-INT-ACR-TO-ORI must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R063,DAT-INT-ACR-TO-ORI length check,DAT-INT-ACR-TO-ORI,exact_length,error,Yes,DAT-INT-ACR-TO-ORI must be exactly 10 characters,10
R064,ACT-STA-ORI numeric,ACT-STA-ORI,numeric,error,Yes,ACT-STA-ORI must be numeric,
R065,ACT-STA-ORI valid values,ACT-STA-ORI,valid_values,error,Yes,ACT-STA-ORI must be one of: 0,0
R066,PCOF-DAT-LAS-PMT-ORI date format,PCOF-DAT-LAS-PMT-ORI,date_format,error,Yes,PCOF-DAT-LAS-PMT-ORI must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R067,DAT-LAS-STM-ORI date format,DAT-LAS-STM-ORI,date_format,error,Yes,DAT-LAS-STM-ORI must be a valid date (MM/DD/CCYY),MM/DD/CCYY
R068,PCOF-PMT-AMT-LAS-ORI numeric,PCOF-PMT-AMT-LAS-ORI,numeric,error,Yes,PCOF-PMT-AMT-LAS-ORI must be numeric,
R069,OGL-PMT-AMT-ORI numeric,OGL-PMT-AMT-ORI,numeric,error,Yes,OGL-PMT-AMT-ORI must be numeric,
R070,DAT-ACT-CLS-TO-ATY-ORI date format,DAT-ACT-CLS-TO-ATY-ORI,date_format,error,Yes,DAT-ACT-CLS-TO-ATY-ORI must be a valid date (MM/DD/CCYY),MM/DD/CCYY
"""

CUS.write_text(CUS_ROWS.lstrip(), encoding="utf-8", newline="")
ORI.write_text(ORI_ROWS.lstrip(), encoding="utf-8", newline="")
print("CUS done:", CUS.stat().st_size, "bytes")
print("ORI done:", ORI.stat().st_size, "bytes")
