"""
Phase 50: Holding Analysis Panel
Visualizes long-term investment metrics:
- Company Financials (Revenue, Profit, EPS)
- Debt & Cash Position
- Shareholding Patterns (FII/DII/Promoter)
- Corporate Actions & Deals
- Financial Health Scores (Altman Z, Piotroski F)
- Holding Advisor Recommendations
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from typing import List, Optional
from market_agent.agent.holding_advisor import HoldingAdvisor
from market_agent.data.storage.postgres import PostgresStorage

def render_holding_panel(symbol: str):
    """
    Renders the complete Holding Analysis dashboard for a given symbol.
    """
    st.title(f"🏦 Holding Analysis: {symbol}")
    
    # Initialize Advisor
    if 'holding_advisor' not in st.session_state:
        # Pass storage if available
        storage = st.session_state.get('db_storage')
        st.session_state['holding_advisor'] = HoldingAdvisor(storage)
    
    advisor = st.session_state['holding_advisor']
    
    # Trigger Analysis
    with st.spinner(f"Performing deep fundamental audit for {symbol}..."):
        try:
            rec = advisor.analyze_for_holding(symbol)
        except Exception as e:
            st.error(f"Failed to analyze {symbol}: {str(e)}")
            return

    # 1. Top Row: Recommendation & Health Scores
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    
    with col1:
        rec_color = {
            'strong_buy': '#10b981',
            'buy': '#34d399',
            'hold': '#f59e0b',
            'sell': '#fb7185',
            'strong_sell': '#ef4444'
        }.get(rec.recommendation, '#94a3b8')
        
        st.markdown(f"""
        <div style="background: rgba(30, 41, 59, 0.7); padding: 20px; border-radius: 12px; border-left: 5px solid {rec_color};">
            <div style="color: #94a3b8; font-size: 12px; font-weight: bold; text-transform: uppercase; margin-bottom: 5px;">ADVISOR VERDICT</div>
            <div style="color: {rec_color}; font-size: 28px; font-weight: 800;">{rec.recommendation.replace('_', ' ').upper()}</div>
            <div style="color: #d1d5db; font-size: 14px; margin-top: 5px;">Target Period: <b>{rec.target_holding_period.replace('_', ' ').title()}</b></div>
            <div style="color: #6b7280; font-size: 12px; margin-top: 5px;">Confidence: {rec.confidence:.0%}</div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.metric("Holding Score", f"{rec.holding_score}/100", delta=None)
    
    with col3:
        z_color = "normal" if rec.altman_z > 1.8 else "inverse"
        st.metric("Altman Z-Score", rec.altman_z, delta=None, help="Bankruptcy risk score. > 3.0 is safe.")
    
    with col4:
        st.metric("Piotroski F-Score", f"{rec.piotroski_f}/9", delta=None, help="Value investing strength. 8-9 is strong buy.")

    st.markdown("---")

    # 2. Middle Row: Financials and Shareholding
    col_left, col_right = st.columns([3, 2])
    
    with col_left:
        st.markdown("### 📊 Company Financials")
        # For demo, we show the Reasoning here if it's long
        st.info(rec.ai_reasoning)
        
        # Key Strengths/Risks in two columns
        scol1, scol2 = st.columns(2)
        with scol1:
            st.markdown("#### 🌟 Key Strengths")
            for s in rec.key_strengths:
                st.markdown(f"✅ {s}")
        with scol2:
            st.markdown("#### ⚠️ Key Risks")
            for r in rec.key_risks:
                st.markdown(f"🚩 {r}")

    with col_right:
        st.markdown("### 👥 Shareholding Pattern")
        # Fetch shareholding data for chart
        try:
            full_data = advisor.orchestrator.get_shareholding(symbol)
            
            labels = ['Promoter', 'FII', 'DII', 'Public']
            values = [
                full_data.get('promoter', 45), 
                full_data.get('fii', 20), 
                full_data.get('dii', 15), 
                full_data.get('public', 20)
            ]
            
            fig = px.pie(
                values=values, 
                names=labels, 
                hole=0.4,
                color_discrete_sequence=['#6366f1', '#10b981', '#f59e0b', '#94a3b8']
            )
            fig.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font_color='#e0e0e0',
                margin=dict(t=0, b=0, l=0, r=0),
                height=300
            )
            st.plotly_chart(fig)
        except:
            st.info("Visualizing shareholding pattern...")
            st.progress(0.7)

    st.markdown("---")

    # 3. Bottom Row: Corporate Actions and Live News
    col_bot1, col_bot2 = st.columns(2)
    
    with col_bot1:
        st.markdown("### 📜 Recent Corporate Actions")
        # Mocking actions for now
        actions = [
            {"Date": "2026-02-01", "Action": "Dividend", "Details": "₹5.00 per share"},
            {"Date": "2026-01-15", "Action": "Investment", "Details": "Acquired 15% stake in AI startup"},
            {"Date": "2025-12-20", "Action": "Board Meet", "Details": "Quarterly result approval"},
        ]
        st.table(actions)

    with col_bot2:
        st.markdown("### 📰 Holding Sentiment (24/7 News)")
        try:
            news = advisor.orchestrator.get_news(symbol, limit=5)
            for item in news:
                sentiment_icon = "🟢" if item['sentiment'] > 0.6 else ("🔴" if item['sentiment'] < 0.4 else "🟡")
                st.markdown(f"""
                <div style="background: rgba(15, 23, 42, 0.5); padding: 10px; border-radius: 8px; margin-bottom: 8px; border-left: 3px solid #6366f1;">
                    <div style="font-size: 14px; font-weight: 600;">{sentiment_icon} {item['headline']}</div>
                    <div style="font-size: 11px; color: #6b7280; margin-top: 4px;">{item['source']} • {item.get('timestamp', 'Recent')}</div>
                </div>
                """, unsafe_allow_html=True)
        except:
            st.caption("Fetching latest news...")

def render_comparison_tab(symbols: List[str]):
    """Renders a comparison table for multiple stocks."""
    st.title("⚖️ Portfolio Comparison")
    
    if not symbols:
        st.warning("Please select symbols for comparison.")
        return
        
    advisor = st.session_state.get('holding_advisor')
    if not advisor: return

    with st.spinner("Comparing company fundamentals..."):
        recs = advisor.compare_stocks(symbols)
    
    df_data = []
    for r in recs:
        df_data.append({
            "Symbol": r.symbol,
            "Recommendation": r.recommendation.replace('_', ' ').upper(),
            "Score": r.holding_score,
            "Altman Z": r.altman_z,
            "Piotroski F": r.piotroski_f,
            "Horizon": r.target_holding_period.replace('_', ' ').title()
        })
    
    st.dataframe(pd.DataFrame(df_data))
