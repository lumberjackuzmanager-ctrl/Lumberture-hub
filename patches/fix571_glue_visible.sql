-- fix571 (18.11, эгаси): клей накладнойда ҳам кўринсин — «Клей» омбори ва бошқа папкадаги клейлар (Mesan: NEFIX, SOMAFIX) cvis 2 -> 1.
-- catalog.mirror() энди шундай ҳисоблайди (VIS_WH + GLUE_RE); бу патч — кейинги mirror'гача кутмаслик учун.
UPDATE erp_goods SET cvis=1 WHERE src='cat' AND cvis=2 AND ext IN ('cn14','ci61','ci62','ci1317','ci1319','ci1326','ci1327','ci1328','ci14537');
