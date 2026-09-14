"""Run with: streamlit run app.py"""
import os
from pathlib import Path
import pandas as pd
import streamlit as st
from src.planner import catalog, load_release, staffing, MODELS

st.set_page_config(page_title='Operations Capacity Planner',page_icon='📊',layout='wide')
st.title('Operations Capacity Planner')
st.caption('Seven-day service demand • Transparent staffing scenarios')
root = Path(os.environ.get('OPS_ARTIFACT_DIR', str(Path(__file__).parent/'artifacts')))
releases, errors = catalog(root)
for error in errors:
    st.warning(f'A forecast release could not be loaded: {error}')
if not releases:
    st.info('No validated forecasts available. Build a release using the commands in RUNBOOK.md.')
    st.stop()
with st.sidebar:
    st.header('Planning scope')
    agency = st.selectbox('Agency',sorted({m['scope']['agency'] for _,m in releases}))
    borough = st.selectbox('Borough',sorted({m['scope']['borough'] for _,m in releases if m['scope']['agency']==agency}))
    matches = sorted([(p,m) for p,m in releases if m['scope']['agency']==agency and m['scope']['borough']==borough],
                     key=lambda pair:(pair[1]['origin'],pair[1]['generated_at']),reverse=True)
    index = st.selectbox('Forecast release',range(len(matches)),format_func=lambda i:f"As of {matches[i][1]['origin']} · {matches[i][0].name}")
    path, meta = matches[index]
    meta, forecast = load_release(path)
    categories = st.multiselect('Categories',meta['scope']['categories'],default=meta['scope']['categories'])
    st.header('Capacity assumptions')
    throughput = st.number_input('Requests per person per day',min_value=0.1,value=20.0,step=1.0)
    available = st.number_input('Available staff per day',min_value=0,value=50,step=1)
    names = {'weekday_mean_4w':'4-week weekday mean','seasonal_naive':'Seasonal naïve','lightgbm':'LightGBM'}
    model = st.selectbox('Forecast method',MODELS,index=MODELS.index(meta['policy']),format_func=lambda k:names[k])
st.subheader(f'{agency} · {borough.title()}')
st.caption(f"Data through {meta['origin']} · Forecast {forecast.target_date.min():%b %d}–{forecast.target_date.max():%b %d, %Y}")
if forecast.target_date.max().date() < pd.Timestamp.now(tz='America/New_York').date():
    st.warning('Historical forecast demonstration. Refresh ingestion and build a new release before planning current operations.')
st.caption(f"Default method selected on validation data: {names[meta['policy']]}. Only prepared agency and borough data appear in the selectors.")
if not categories:
    st.info('Select at least one category to calculate a staffing scenario.')
    st.stop()
scenario = staffing(forecast,categories,model,throughput,available)
a,b,c,d = st.columns(4)
a.metric('Forecast requests · 7 days',f'{scenario.forecast_requests.sum():,.0f}')
b.metric('Peak daily staff needed',f'{scenario.required_staff.max():,}')
c.metric('Staff-days needed',f'{scenario.required_staff.sum():,}')
d.metric('Requests above capacity',f'{scenario.unserved_requests.sum():,.0f}')
st.line_chart(scenario.set_index('target_date')[['forecast_requests','available_capacity']],color=['#2563eb','#f97316'],x_label='Forecast date',y_label='Requests per day')
st.caption('Staffing pools capacity across selected categories. Staff-days sum daily positions, not unique employees. Unserved requests do not carry into the next day.')
st.dataframe(scenario,hide_index=True,use_container_width=True)
st.download_button('Download this scenario',scenario.to_csv(index=False),'capacity_scenario.csv','text/csv')
with st.expander('Observed demand history'):
    history = pd.read_csv(path/'history.csv',parse_dates=['demand_date'])
    history = history[history.category.isin(categories) & history.is_complete.eq(True)]
    st.line_chart(history.pivot(index='demand_date',columns='category',values='request_count').tail(56),y_label='Observed requests')
with st.expander('Model evaluation and evidence',expanded=True):
    st.markdown(f"**Matched evaluation: {meta['evaluation_start']} to {meta['evaluation_end']}**")
    metrics = pd.read_csv(path/'evaluation.csv')
    metrics['WAPE (%)'] = 100*metrics.wape
    st.dataframe(metrics[['model','n','mae','WAPE (%)','bias']],hide_index=True,use_container_width=True)
    full = pd.read_csv(path/'baseline_full.csv')
    benchmark = full[full.model=='weekday_mean_4w'].iloc[0]
    st.caption(f"Full-period weekday-mean benchmark: {benchmark.wape:.2%} WAPE ({meta['baseline_start']}–{meta['baseline_end']}). Different evaluation dates: compare model rows above against each other, not against this longer-period figure. Bias = actual − prediction; positive means underforecasting.")
    st.caption(meta['caveat'])
    st.caption('Throughput is your assumption, not measured productivity. No SLA, queueing, shift coverage, absence, service-time mix, or financial savings are inferred. Federal observed holidays are calendar features, not agency closure indicators.')
