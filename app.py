"""
app.py - the web interface. Start it with:  streamlit run app.py
"""
import streamlit as st

from engine import DEFAULT_SETTINGS, run_tests, to_excel

st.set_page_config(page_title="Journal Entry Tester", layout="wide")
st.title("Journal Entry Tester (SAF-T, ISA 240)")
st.write("Upload a SAF-T Financial XML file. The tool scores every voucher against "
         "ISA 240 risk characteristics and returns an Excel file with the results.")

# ----- Settings in the sidebar -----
st.sidebar.header("Settings")
pm = st.sidebar.number_input("Performance materiality (NOK)", min_value=0,
                             value=DEFAULT_SETTINGS["performance_materiality"], step=10_000)
days_end = st.sidebar.number_input("Days before period end", min_value=0,
                                   value=DEFAULT_SETTINGS["days_before_period_end"])
backdate = st.sidebar.number_input("Backdated if entered more than X days after date", min_value=0,
                                   value=DEFAULT_SETTINGS["backdating_days"])
reversal = st.sidebar.number_input("Quick reversal within X days", min_value=0,
                                   value=DEFAULT_SETTINGS["reversal_days"])
top_n = st.sidebar.number_input("Number of top-risk vouchers", min_value=5,
                                value=DEFAULT_SETTINGS["top_n"], step=5)
manual = st.sidebar.text_input("Manual journal IDs (comma separated)",
                               ", ".join(DEFAULT_SETTINGS["manual_journals"]))
weak = st.sidebar.text_input("Weak description words (comma separated)",
                             ", ".join(DEFAULT_SETTINGS["weak_words"]))

settings = {
    "performance_materiality": pm,
    "days_before_period_end": days_end,
    "backdating_days": backdate,
    "reversal_days": reversal,
    "top_n": int(top_n),
    "manual_journals": [x.strip() for x in manual.split(",") if x.strip()],
    "weak_words": [x.strip() for x in weak.split(",") if x.strip()],
}

# ----- Upload and run ----
st.warning("Demo version: use only the fictional demo file by checking the empty box under the Upload button. Never upload real client data.")
uploaded = st.file_uploader("Choose a SAF-T XML file", type=["xml"])
if uploaded is None and st.checkbox("No file? Use the fictional demo file"):
    uploaded = "saft.xml"


if uploaded is not None:
    try:
        with st.spinner("Running tests..."):
            results = run_tests(uploaded, settings)
    except Exception as e:
        st.error(f"Could not process the file: {e}")
        st.stop()

    s = results["summary"]
    st.subheader(s["company"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Period end", str(s["period_end"]))
    c2.metric("Vouchers", f"{s['vouchers']:,}")
    c3.metric("Postings", f"{s['postings']:,}")
    c4.metric("Debit - credit", f"{s['balance_check']:,.2f}")

    if s["balance_check"] != 0:
        st.warning("Debits and credits do not balance. Check that the file was read correctly.")

    st.download_button(
        "Download Excel report",
        data=to_excel(results),
        file_name="journal_entry_test.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )

    st.subheader("Top risk vouchers")
    st.dataframe(results["top"], use_container_width=True)

    left, right = st.columns(2)
    with left:
        st.subheader("How often each rule fired")
        st.dataframe(results["rule_counts"], hide_index=True, use_container_width=True)
    with right:
        st.subheader("Benford's law")
        st.caption(f"N = {s['benford_n']:,} | MAD = {s['benford_mad']} ({s['benford_conformity']}) "
                   f"| points per flagged voucher = {s['benford_points']}")
        st.bar_chart(results["benford"].set_index("digit")[["expected %", "actual %"]])
else:
    st.info("Upload a file to start.")