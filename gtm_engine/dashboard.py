"""
Dashboard — run with: streamlit run gtm_engine/dashboard.py
Shows sourced leads, their pain points, drafted outreach, and the
follow-up queue. This is the "founder can see it working" surface.
"""
import streamlit as st
import pandas as pd

from gtm_engine import orchestrator
from gtm_engine.crm import db

st.set_page_config(page_title="Autonomous GTM Engine", layout="wide")
st.title("🚀 Autonomous GTM Engine")
st.caption("Target ICP → Apollo → Enrichment → Research → Pain Points → Email/LinkedIn → CRM → Follow-ups")

db.init_db()

with st.sidebar:
    st.header("Run a campaign")
    max_leads = st.slider("Leads to pull", 1, 20, 5)
    if st.button("Run pipeline now", type="primary"):
        with st.spinner("Running full pipeline..."):
            results = orchestrator.run_pipeline(max_leads=max_leads)
        st.success(f"Sequenced {len(results)} leads")

    st.divider()
    st.caption("Synthetic demo only — general funnel events only; these events are not eligible for V5 learning.")
    if st.button("Simulate engagement (demo)"):
        import random
        leads = db.all_leads()
        count = 0
        for lead in leads:
            lid = lead["id"]
            if random.random() < 0.7:
                db.record_event(lid, "email_opened"); count += 1
            if random.random() < 0.35:
                db.record_event(lid, "email_replied"); count += 1
            if random.random() < 0.15:
                db.record_event(lid, "meeting_booked"); count += 1
            if random.random() < 0.05:
                db.record_event(lid, "won"); count += 1
        st.success(f"Simulated {count} engagement events")

tab1, tab2, tab3, tab4 = st.tabs(["Leads (CRM)", "Outreach queue", "Drafted copy", "Analytics"])

with tab1:
    leads = db.all_leads()
    if leads:
        st.dataframe(pd.DataFrame(leads), use_container_width=True)
    else:
        st.info("No leads yet — run a campaign from the sidebar.")

with tab2:
    outreach = db.all_outreach()
    if outreach:
        df = pd.DataFrame(outreach)
        st.dataframe(df, use_container_width=True)
        st.metric("Scheduled follow-ups", int((df["status"] == "scheduled").sum()))
    else:
        st.info("No outreach drafted yet.")

with tab3:
    outreach = db.all_outreach()
    for row in outreach:
        if row["sequence_step"] == 0:
            with st.expander(f"Lead #{row['lead_id']} — {row['channel']}"):
                if row["subject"]:
                    st.write(f"**Subject:** {row['subject']}")
                st.write(row["body"])

with tab4:
    st.subheader("Funnel")
    funnel = db.funnel_counts()
    funnel_df = pd.DataFrame(
        {"stage": list(funnel.keys()), "leads": list(funnel.values())}
    ).set_index("stage")
    st.bar_chart(funnel_df)
    st.dataframe(funnel_df.reset_index(), use_container_width=True, hide_index=True)

    st.subheader("LLM cost & token usage")
    cost = db.cost_summary()
    c1, c2, c3 = st.columns(3)
    c1.metric("Total cost (USD)", f"${cost['total_cost_usd']:.4f}")
    c2.metric("Avg cost / lead (USD)", f"${db.cost_per_lead():.4f}")
    c3.metric("Total tokens", cost["total_input_tokens"] + cost["total_output_tokens"])

    if cost["by_agent"]:
        by_agent_df = pd.DataFrame(cost["by_agent"]).T
        by_agent_df.index.name = "agent"
        st.dataframe(by_agent_df, use_container_width=True)
        st.bar_chart(by_agent_df["cost_usd"])
    else:
        st.info("No LLM usage recorded yet — run a campaign from the sidebar.")

    st.subheader("Reply classification (adaptive follow-ups)")
    reply_counts = db.reply_classification_counts()
    if reply_counts:
        reply_df = pd.DataFrame(
            {"classification": list(reply_counts.keys()), "count": list(reply_counts.values())}
        ).set_index("classification")
        st.bar_chart(reply_df)
    else:
        st.info("No replies classified yet — send a reply via POST /events/track with reply_text.")

    st.subheader("A/B test — email copy")
    from gtm_engine.agents import ab_test as ab_test_module
    winner = db.get_validated_active_winner()
    if winner:
        st.success(f"V5-validated variant **{winner['variant']}** is active under the 80/20 exploration policy.")
    else:
        st.info("No V5-validated winner yet — new assignments remain randomized 50/50.")

    if st.button("Evaluate & promote winner"):
        decision = ab_test_module.evaluate_and_promote()
        if decision["decided"]:
            margin_text = f"{decision['margin']}x reply-rate ratio" if decision.get("margin") is not None else "runner-up reply rate was zero"
            st.success(f"Validated variant {decision['winner']} ({decision['metric']} = {decision['value']}; {margin_text})")
        else:
            st.warning(decision["reason"])

    st.divider()
    st.subheader("Outcome Feedback — V5")
    st.caption("Real attributed outcomes only. Lead-level legacy events and synthetic demo events are excluded from optimization evidence.")
    from gtm_engine.optimization.feedback import OutcomeFeedbackService
    try:
        feedback_summary = OutcomeFeedbackService.get_dataset_summary()
        recommendation = OutcomeFeedbackService.get_recommendation()
        cohort = feedback_summary["cohort"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Decisions", cohort["total_decisions"])
        m2.metric("Sent exposures", cohort["sent_exposures"])
        m3.metric("Mature exposures", cohort["mature_exposures"])
        m4.metric("Unattributed events excluded", cohort["unattributed_events_count"])
        st.write("**Variant evidence (mature, explicitly attributed first-touch emails)**")
        rows = []
        for variant, values in feedback_summary["variants"].items():
            rows.append({
                "Variant": variant, "Style": values["action_value"],
                "Mature sent": values["mature_sent"], "Replies": values["counts"]["replied"],
                "Reply rate": values["rates"]["reply_rate"], "Reply 95% CI": values["rates"]["reply_ci"],
                "Meetings": values["counts"]["meetings"], "Wins": values["counts"]["won"],
                "Bounces": values["counts"]["bounced"], "Unsubscribes": values["counts"]["unsubscribed"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.write("**Recommendation (advisory; no automatic sending or promotion)**")
        st.json(recommendation)
        if feedback_summary["data_quality_warnings"]:
            for warning in feedback_summary["data_quality_warnings"]:
                st.warning(warning)
        else:
            st.info("No current attribution-quality warnings.")
        if st.button("Evaluate evidence and validate promotion", key="v5_evaluate_promotion"):
            decision = ab_test_module.evaluate_and_promote()
            if decision.get("decided"):
                st.success(f"Validated recommendation: variant {decision['winner']} — {decision.get('reason', '')}")
            else:
                st.warning(decision.get("reason", "Insufficient evidence"))
    except Exception as exc:
        st.warning(f"Outcome feedback is unavailable until the V5 schema/data are initialized: {exc}")

    perf = db.variant_performance()
    if perf:
        perf_df = pd.DataFrame(perf).T
        st.dataframe(perf_df, use_container_width=True)
        st.bar_chart(perf_df[["open_rate", "reply_rate", "meeting_rate"]])
    else:
        st.info("No variant data yet — run a campaign from the sidebar.")
