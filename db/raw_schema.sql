-- Raw layer: the four SEC Financial Statement Data Set files, loaded as-is (SPEC §4.2).
-- Types follow readme.htm (2025q1). The loader creates the schema and sets search_path
-- before running this file, so nothing here names a schema. Every statement is
-- idempotent; the loader truncates before each COPY.

CREATE TABLE IF NOT EXISTS sub (
    adsh        TEXT PRIMARY KEY,         -- accession number, nnnnnnnnnn-nn-nnnnnn
    cik         BIGINT NOT NULL,
    name        TEXT NOT NULL,
    sic         INTEGER,
    countryba   TEXT,
    stprba      TEXT,
    cityba      TEXT,
    zipba       TEXT,
    bas1        TEXT,
    bas2        TEXT,
    baph        TEXT,
    countryma   TEXT,
    stprma      TEXT,
    cityma      TEXT,
    zipma       TEXT,
    mas1        TEXT,
    mas2        TEXT,
    countryinc  TEXT,
    stprinc     TEXT,
    ein         BIGINT,
    former      TEXT,
    changed     DATE,                     -- yyyymmdd in the file
    afs         TEXT,                     -- filer status, e.g. 1-LAF
    wksi        BOOLEAN NOT NULL,
    fye         TEXT,                     -- fiscal year end, mmdd
    form        TEXT NOT NULL,            -- 10-K, 10-K/A, 10-Q, ...
    period      DATE,                     -- balance sheet date; a few filings leave it empty
    fy          INTEGER,
    fp          TEXT,                     -- FY, Q1, Q2, Q3, Q4
    filed       DATE NOT NULL,
    accepted    TIMESTAMP NOT NULL,
    prevrpt     BOOLEAN NOT NULL,
    detail      BOOLEAN NOT NULL,
    instance    TEXT NOT NULL,
    nciks       INTEGER NOT NULL,
    aciks       TEXT
);
CREATE INDEX IF NOT EXISTS sub_cik_idx ON sub (cik);
CREATE INDEX IF NOT EXISTS sub_form_idx ON sub (form);

CREATE TABLE IF NOT EXISTS tag (
    tag         TEXT NOT NULL,
    version     TEXT NOT NULL,            -- taxonomy (us-gaap/2024) or adsh for custom tags
    custom      BOOLEAN NOT NULL,
    abstract    BOOLEAN NOT NULL,
    datatype    TEXT,
    iord        TEXT,                     -- I = instant (balance), D = duration (flow)
    crdr        TEXT,                     -- C or D, natural balance
    tlabel      TEXT,
    doc         TEXT,
    PRIMARY KEY (tag, version)
);

-- The SEC key is (adsh, tag, version, ddate, qtrs, uom, segments, coreg). segments and
-- coreg are NULL for consolidated facts, and 2025q1 has 14 rows that repeat the full key
-- with different values (one filer's derivative disclosures), so there is no primary key.
-- The marts only use rows with segments IS NULL AND coreg IS NULL; staging deduplicates.
CREATE TABLE IF NOT EXISTS num (
    adsh        TEXT NOT NULL,
    tag         TEXT NOT NULL,
    version     TEXT NOT NULL,
    ddate       DATE NOT NULL,            -- period end, yyyymmdd in the file
    qtrs        INTEGER NOT NULL,         -- 0 = instant, 4 = full year
    uom         TEXT NOT NULL,
    segments    TEXT,                     -- axis=member; ... , NULL for the consolidated figure
    coreg       TEXT,                     -- co-registrant, NULL for the consolidated figure
    value       NUMERIC,                  -- NUMERIC(28,4) in the readme; stored exactly
    footnote    TEXT
);
CREATE INDEX IF NOT EXISTS num_adsh_tag_idx ON num (adsh, tag);

CREATE TABLE IF NOT EXISTS pre (
    adsh        TEXT NOT NULL,
    report      INTEGER NOT NULL,
    line        INTEGER NOT NULL,
    stmt        TEXT,                     -- BS, IS, CF, EQ, CI, CP, UN; empty for 228 rows in 2025q1
    inpth       BOOLEAN NOT NULL,
    rfile       TEXT NOT NULL,
    tag         TEXT NOT NULL,
    version     TEXT NOT NULL,
    plabel      TEXT,                     -- empty for 149 rows in 2025q1 despite the readme
    negating    BOOLEAN NOT NULL,
    PRIMARY KEY (adsh, report, line)
);
