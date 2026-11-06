"""
engine.py - the journal entry tester itself (no interface).
app.py calls run_tests() and to_excel().
"""
import io
import math
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import pandas as pd

NS = {"n": "urn:StandardAuditFile-Taxation-Financial:NO"}

DEFAULT_SETTINGS = {
    "performance_materiality": 250_000,
    "days_before_period_end": 5,
    "backdating_days": 30,
    "reversal_days": 7,
    "night_start": 22,
    "night_end": 6,
    "weak_words": ["korr", "diverse", "div", "rettelse", "test", "xx", "?"],
    "manual_journals": ["GL", "MAN", "MANUELL"],
    "top_n": 50,
}

DEFAULT_POINTS = {
    "Weekend": 2, "Holiday": 2, "Night": 2,
    "Near period end": 2, "Entered after period end": 3, "Backdated": 3,
    "Round amount": 1, "Above PM": 3, "Weak description": 1,
    "Rare account combination": 2, "Manual journal": 1, "Unusual user": 2,
    "Quick reversal": 3, "Possible duplicate": 3, "Benford digit": 1,
}

BENFORD_MAX_POINTS = 3
BENFORD_MIN_N = 500       # at or below this: 0 points
BENFORD_FULL_N = 5_000    # at or above this: full points


# ---------------- 1. READ THE XML ----------------
def read_saft(file):
    """file can be a file name or an uploaded file object."""
    root = ET.parse(file).getroot()

    end_text = (root.findtext(".//n:Header/n:SelectionCriteria/n:SelectionEndDate", namespaces=NS)
                or root.findtext(".//n:Header/n:EndDate", namespaces=NS))
    if end_text is None:
        raise ValueError("Could not find the period end date in the file header.")
    period_end = pd.to_datetime(end_text)

    company = root.findtext(".//n:Header/n:Company/n:Name", default="(unknown company)", namespaces=NS)

    accounts = {a.findtext("n:AccountID", namespaces=NS): a.findtext("n:AccountDescription", namespaces=NS)
                for a in root.iterfind(".//n:GeneralLedgerAccounts/n:Account", NS)}

    rows = []
    for journal in root.iterfind(".//n:GeneralLedgerEntries/n:Journal", NS):
        journal_id = journal.findtext("n:JournalID", namespaces=NS)
        journal_type = journal.findtext("n:Type", namespaces=NS)
        for tx in journal.iterfind("n:Transaction", NS):
            for line in tx.iterfind("n:Line", NS):
                debit = line.findtext("n:DebitAmount/n:Amount", default="0", namespaces=NS)
                credit = line.findtext("n:CreditAmount/n:Amount", default="0", namespaces=NS)
                rows.append({
                    "journal": journal_id, "journal_type": journal_type,
                    "voucher": tx.findtext("n:TransactionID", namespaces=NS),
                    "date": tx.findtext("n:TransactionDate", namespaces=NS),
                    "entered": tx.findtext("n:SystemEntryDate", namespaces=NS),
                    "entry_time": line.findtext("n:SystemEntryTime", namespaces=NS),
                    "user": tx.findtext("n:SourceID", namespaces=NS),
                    "description": (line.findtext("n:Description", namespaces=NS)
                                    or tx.findtext("n:Description", namespaces=NS)),
                    "account": line.findtext("n:AccountID", namespaces=NS),
                    "amount": float(debit) - float(credit),
                })

    if not rows:
        raise ValueError("No transactions found. Is this a SAF-T Financial file?")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["entered"] = pd.to_datetime(df["entered"])
    df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
    df["description"] = df["description"].fillna("")
    df["account_name"] = df["account"].map(accounts)
    return df, period_end, company


# ---------------- 2. NORWEGIAN PUBLIC HOLIDAYS ----------------
def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    return date(y, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)


def norwegian_holidays(y):
    e = easter(y)
    fixed = [date(y, 1, 1), date(y, 5, 1), date(y, 5, 17), date(y, 12, 25), date(y, 12, 26)]
    moving = [e + timedelta(days=x) for x in (-3, -2, 0, 1, 39, 49, 50)]
    return set(fixed + moving)


# ---------------- 3. RULES + SCORING ----------------
def run_tests(file, settings=None, points=None):
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    points = {**DEFAULT_POINTS, **(points or {})}

    df, period_end, company = read_saft(file)

    holidays = set()
    for y in df["date"].dt.year.unique():
        holidays |= norwegian_holidays(int(y))

    df["Weekend"] = df["date"].dt.weekday >= 5
    df["Holiday"] = df["date"].dt.date.isin(holidays)
    hour = df["entry_time"].dt.hour
    df["Night"] = (hour >= s["night_start"]) | (hour < s["night_end"])
    df["Near period end"] = ((df["date"] >= period_end - pd.Timedelta(days=s["days_before_period_end"]))
                             & (df["date"] <= period_end))
    df["Entered after period end"] = (df["entered"] > period_end) & (df["date"] <= period_end)
    df["Backdated"] = (df["entered"] - df["date"]).dt.days > s["backdating_days"]
    df["Round amount"] = (df["amount"].abs() >= 10_000) & (df["amount"].abs() % 1_000 == 0)
    df["Above PM"] = df["amount"].abs() > s["performance_materiality"]
    desc = df["description"].str.strip().str.lower()
    weak = [w.lower() for w in s["weak_words"]]
    df["Weak description"] = (desc.str.len() < 4) | desc.apply(
        lambda t: any(w == t or t.startswith(w + " ") for w in weak))
    df["Manual journal"] = df["journal"].isin(s["manual_journals"]) | df["journal_type"].isin(s["manual_journals"])

    combo = df.groupby("voucher")["account"].apply(lambda a: " + ".join(sorted(set(a))))
    df["Rare account combination"] = df["voucher"].map(combo.map(combo.value_counts()) <= 2)

    user_share = df.groupby("user")["voucher"].nunique() / df["voucher"].nunique()
    df["Unusual user"] = df["user"].map(user_share < 0.02)

    df["Quick reversal"] = False
    for _, grp in df.groupby([df["account"], df["amount"].abs()]):
        pos, neg = grp[grp["amount"] > 0], grp[grp["amount"] < 0]
        for i, p in pos.iterrows():
            for j, q in neg.iterrows():
                if p["voucher"] != q["voucher"] and abs((p["date"] - q["date"]).days) <= s["reversal_days"]:
                    df.loc[[i, j], "Quick reversal"] = True

    dup = df.groupby(["date", "amount", "account"])["voucher"].transform("nunique")
    df["Possible duplicate"] = (dup > 1) & (df["amount"].abs() >= 1_000)

    # Benford, weighted by population size
    amounts = df["amount"].abs()
    benford_pop = amounts[amounts >= 10]
    n = len(benford_pop)
    first = pd.to_numeric(benford_pop.astype(str).str.lstrip("0.").str[0], errors="coerce")
    actual = first.value_counts(normalize=True)
    expected = {d: math.log10(1 + 1 / d) for d in range(1, 10)}

    if n <= BENFORD_MIN_N:
        weight = 0.0
    elif n >= BENFORD_FULL_N:
        weight = 1.0
    else:
        weight = ((math.log10(n) - math.log10(BENFORD_MIN_N))
                  / (math.log10(BENFORD_FULL_N) - math.log10(BENFORD_MIN_N)))
    points["Benford digit"] = round(BENFORD_MAX_POINTS * weight, 2)

    z_scores = {}
    for d in range(1, 10):
        p, o = expected[d], actual.get(d, 0)
        z_scores[d] = (abs(o - p) - 1 / (2 * n)) / math.sqrt(p * (1 - p) / n) if n else 0
    over = [d for d in range(1, 10) if actual.get(d, 0) > expected[d] and z_scores[d] > 1.96]

    mad = sum(abs(actual.get(d, 0) - expected[d]) for d in range(1, 10)) / 9
    conformity = ("Close conformity" if mad <= 0.006 else "Acceptable conformity" if mad <= 0.012
                  else "Marginal conformity" if mad <= 0.015 else "Nonconformity")

    df["Benford digit"] = False
    df.loc[benford_pop.index, "Benford digit"] = first.isin(over).values
    benford = pd.DataFrame({
        "digit": range(1, 10),
        "expected %": [round(expected[d] * 100, 1) for d in range(1, 10)],
        "actual %": [round(actual.get(d, 0) * 100, 1) for d in range(1, 10)],
        "z-score": [round(z_scores[d], 2) for d in range(1, 10)],
        "flagged": [d in over for d in range(1, 10)],
    })

    # Score each voucher
    rules = list(points)
    df[rules] = df[rules].fillna(False).astype(bool)
    v = df.groupby("voucher").agg(
        date=("date", "first"), entered=("entered", "first"), user=("user", "first"),
        description=("description", "first"),
        accounts=("account", lambda a: ", ".join(sorted(set(a)))),
        amount=("amount", lambda a: a[a > 0].sum()),
        **{r: (r, "any") for r in rules})
    v["score"] = round(sum(v[r] * points[r] for r in rules), 2)
    v["reasons"] = v[rules].apply(lambda row: "; ".join(r for r in rules if row[r]), axis=1)

    cols = ["date", "entered", "user", "description", "accounts", "amount", "score", "reasons"]
    vouchers = v[cols].sort_values(["score", "amount"], ascending=False)
    top = vouchers.head(s["top_n"])

    rule_counts = pd.DataFrame({
        "rule": rules,
        "points": [points[r] for r in rules],
        "vouchers flagged": [int(v[r].sum()) for r in rules],
    })

    summary = {
        "company": company,
        "period_end": period_end.date(),
        "postings": len(df),
        "vouchers": df["voucher"].nunique(),
        "balance_check": round(df["amount"].sum(), 2),
        "benford_n": n,
        "benford_points": points["Benford digit"],
        "benford_mad": round(mad, 4),
        "benford_conformity": conformity,
    }

    return {"summary": summary, "top": top, "vouchers": vouchers, "postings": df,
            "benford": benford, "rule_counts": rule_counts}


# ---------------- 4. EXCEL OUTPUT (in memory, for download) ----------------
def to_excel(results):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as xl:
        results["top"].to_excel(xl, sheet_name="Top risk")
        results["vouchers"].to_excel(xl, sheet_name="All vouchers")
        results["postings"].to_excel(xl, sheet_name="All postings", index=False)
        results["rule_counts"].to_excel(xl, sheet_name="Rules", index=False)
        results["benford"].to_excel(xl, sheet_name="Benford", index=False)
    return buffer.getvalue()