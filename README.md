# Real Exchange Rate App

Streamlit app that computes and plots the real exchange rate between any two IMF countries (plus the Euro area, G163) using monthly IMF CPI and exchange rate data from January 1976 where available.

## Definitions

S(t) is units of Country 2 currency per one unit of Country 1 currency. IMF reports exchange rates as local currency per USD (FX), so S(t) = FX2(t) / FX1(t), with FX = 1 for the United States.

RER(t) = S(t) × P1(t) / P2(t), where P1 and P2 are the monthly CPIs.

RER* is the average of RER(t) over valid months in the selected sample. The app plots Real Index(t) = 100 × RER(t) / RER*, recalculated whenever the dates change. Nominal S(t) is plotted in actual currency units.

## Features

Country selection, start and end month selection, Real / Nominal / Both chart modes (Both uses separate y axes), and a CSV download of every observation and calculation in the sample.

## Data

IMF SDMX 3.0 API: https://api.imf.org/external/sdmx/3.0
CPI key: {ISO3}.CPI._T.IX.M (dataflow IMF.STA/CPI)
Exchange rate key: {ISO3}.XDC_USD.PA_RT.M (dataflow IMF.STA/ER)

No API key is required. Coverage varies by country and some series have gaps; months missing any input are excluded from RER*.

## Run locally

```
pip install -r requirements.txt
streamlit run app.py
```

Built with Python 3.12. Code generated with Claude (Anthropic), October 2026.
