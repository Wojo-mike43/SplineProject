import numpy as np
import pandas as pd
from scipy.interpolate import make_lsq_spline
from scipy.optimize import brentq
import plotly.express as px
import streamlit as st

# Globals
pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
DATA_DATE = pd.Timestamp(year=2026, month=11, day=5)
SETTLE_DATE = pd.Timestamp(year=2026, month=11, day=6)


def calc_accrued_interest(row, prev_cpn_dt, nxt_cpn_dt):
    accrued_days = (SETTLE_DATE - prev_cpn_dt).days
    accrued_period = (nxt_cpn_dt - prev_cpn_dt).days
    accrued_interest = (row['Rate'] / 2) * accrued_days / accrued_period
    dirty_price = row['End of Day'] + accrued_interest
    return accrued_interest, dirty_price

def build_bond_cashflows(row):
    is_end_of_month = row['Maturity Date'].is_month_end
    cashflow_dates = []
    i = 0

    while True:
        date = row['Maturity Date'] - pd.DateOffset(months=6 * i)
        if is_end_of_month:
            date = date + pd.offsets.MonthEnd(0)
        if date <= SETTLE_DATE:
            if is_end_of_month:
                prev_cpn_dt = date + pd.offsets.MonthEnd(0)
            else:
                prev_cpn_dt = date
            break
        cashflow_dates.append(date)
        i += 1

    cashflow_dates = sorted(cashflow_dates)
    cashflow_days = [(date - DATA_DATE).days for date in cashflow_dates]
    bond_cashflows = [(row['Rate'] / 2) for x in cashflow_days]
    bond_cashflows[-1] = bond_cashflows[-1] + 100

    bond_cashflows_df = pd.DataFrame()
    bond_cashflows_df['days_to_maturity'] = cashflow_days
    bond_cashflows_df['discount_factor'] = np.nan
    bond_cashflows_df['bond_cashflows'] = bond_cashflows

    return bond_cashflows_df, prev_cpn_dt, cashflow_dates[0]

def dynamic_rate_search(bond_df, bond_price, low_guess=0, high_guess=1, tol=1e-6, max_iter=100):
    for _ in range(max_iter):
        rate_guess = (low_guess + high_guess) / 2
        temp_df = bond_df.copy()

        last_idx = temp_df.last_valid_index()
        temp_df.loc[last_idx, "discount_factor"] = rate_guess

        log_df = np.log(temp_df["discount_factor"]).replace(np.inf, np.nan)
        log_df_interp = log_df.interpolate(method="index")
        temp_df["discount_factor"] = np.exp(log_df_interp)
        temp_df['discounted_cashflows'] = temp_df["bond_cashflows"] * temp_df['discount_factor']
        bond_pv = temp_df['discounted_cashflows'].sum()

        if abs(bond_pv - bond_price) < tol:
            break
        else:
            if bond_pv > bond_price:
                high_guess = rate_guess
            else:
                low_guess = rate_guess
    return last_idx, rate_guess

def find_zspread(df, dirty_price):
    t = df['days_to_maturity'].values / 365
    dfs = df['discount_factor'].values
    cfs = df['bond_cashflows'].values

    pv = lambda z: (cfs * dfs * np.exp(-z * t)).sum()
    z_spread = brentq(lambda z: pv(z) - dirty_price, -0.05, 0.05) * 1e4
    return z_spread

def build_model():
    df = pd.read_excel("Govies_Data_OTR.xlsx", engine='openpyxl')

    df['Maturity Date'] = pd.to_datetime(df['Maturity Date'])
    df['days_to_maturity'] = (df['Maturity Date'] - DATA_DATE).dt.days
    df = df.sort_values(by='days_to_maturity', ascending=True)

    df['Rate'] = df['Rate'] * 100

    is_on_the_run = df['On the Run'] == 'Y'
    is_off_the_run = df['On the Run'] == 'N'

    is_bill = df["Security Type"] == 'MARKET BASED BILL'
    is_bond = df["Security Type"] != 'MARKET BASED BILL'

    on_the_run_bills = df[is_on_the_run & is_bill].copy()
    on_the_run_bonds = df[is_on_the_run & is_bond].copy()
    off_the_run_bonds = df[is_off_the_run & is_bond].copy()
    off_the_run_bonds = off_the_run_bonds[
        off_the_run_bonds['days_to_maturity'] >= 730].copy()

    # Step 1: Construct Front-End of Discount Factor Curve
    on_the_run_bills['discount_factor'] = on_the_run_bills['End of Day'] / 100
    anchor = pd.DataFrame({'days_to_maturity': [0], 'discount_factor': [1.0]})
    discount_factors_df = (pd.concat(
        [anchor, on_the_run_bills[['days_to_maturity', 'discount_factor']]],
        ignore_index=True).sort_values('days_to_maturity').reset_index(
        drop=True))

    # Step 2: Bootstrap Yield Curve
    for index, row in on_the_run_bonds.iterrows():
        bond_cashflows_df, prev_cpn_dt, nxt_cpn_dt = build_bond_cashflows(row)

        combined_df = pd.concat(
            [discount_factors_df, bond_cashflows_df]).set_index(
            ['days_to_maturity']).sort_index()
        combined_df['bond_cashflows'] = combined_df['bond_cashflows'].fillna(0)

        accrued_interest, dirty_price = calc_accrued_interest(row, prev_cpn_dt,
                                                              nxt_cpn_dt)
        on_the_run_bonds.loc[index, 'accrued_interest'] = accrued_interest
        on_the_run_bonds.loc[index, 'dirty_price'] = dirty_price

        days, discount_factor = dynamic_rate_search(combined_df, dirty_price)
        discount_factors_df.loc[len(discount_factors_df)] = {
            "days_to_maturity": days, "discount_factor": discount_factor}

    discount_factors_df['zero_rates'] = - np.log(
        discount_factors_df['discount_factor']) / (discount_factors_df[
                                                       'days_to_maturity'] / 365)

    # Step 3: Build Spline
    spline_df = discount_factors_df[discount_factors_df['days_to_maturity'] > 0]

    x = spline_df['days_to_maturity'] / 365
    y = spline_df['zero_rates']

    knots = np.r_[[0] * 4, [1, 2, 5, 10, 15, 20, 25], [31] * 4]
    curve = make_lsq_spline(x, y, knots, 3)

    # Step 4: Model Off-the-run spreads
    spread_dict = {}
    for index, row in off_the_run_bonds.iterrows():
        model_spreads_df, prev_cpn_dt, nxt_cpn_dt = build_bond_cashflows(row)

        otr_accrued_interest, otr_dirty_price = calc_accrued_interest(row, prev_cpn_dt, nxt_cpn_dt)
        off_the_run_bonds.loc[index, 'accrued_interest'] = otr_accrued_interest
        off_the_run_bonds.loc[index, 'dirty_price'] = otr_dirty_price

        new_x = model_spreads_df['days_to_maturity']
        model_spreads_df['discount_factor'] = np.exp(
            -(curve(new_x / 365)) * (new_x / 365))

        z_spread = find_zspread(model_spreads_df, otr_dirty_price)
        spread_dict[index] = z_spread

    # Step 5: Final Bond DF with Spreads
    off_the_run_bonds['z_spread_bp'] = pd.Series(spread_dict)
    final_df = off_the_run_bonds[
        ['CUSIP', 'Maturity Date', 'days_to_maturity', 'Rate', 'End of Day', 'dirty_price',
         'z_spread_bp']]

    return curve, x, y, final_df

def plot_curve(curve):
    t = np.linspace(0.1, 30, 300)
    df = pd.DataFrame({
        'Years': t,
        'Zero (%)': curve(t) * 100,
        'Forward (%)': (curve(t) + t * curve.derivative()(t)) * 100,
    })
    fig_plot1 = px.line(df, x='Years', y=['Zero (%)', 'Forward (%)'])
    return fig_plot1

def plot_scatter(df):
    df = df.assign(Years=df['days_to_maturity'] / 365)
    fig_plot2 = px.scatter(df, x='Years', y='z_spread_bp', color='Rate', hover_name='CUSIP')
    return fig_plot2


if __name__ == '__main__':
    curve, x, y, final_df = build_model()

    st.title("Treasury Spline Curve")
    # top of page info
    st.markdown('#### Project Summary')

    # Plot 0 rates vs Maturity
    st.write(f"Figure 1: zero-rates vs forward implied rate")
    fig_plot1 = plot_curve(curve)
    st.plotly_chart(fig_plot1)

    # Plot RV Scatter
    st.write(f"Figure 2: Scatter plot of years vs z-spread in bps")
    fig_plot2 = plot_scatter(final_df)
    st.plotly_chart(fig_plot2)

    # Table
    st.write(f"Figure 3: Output data ordered by highest z-spread")
    st.dataframe(final_df.sort_values('z_spread_bp'))

    # Model Description
    st.markdown('#### Model Description')
    with st.expander("Click Here To Read More About The Model:"):
        st.write("Builds a US Treasury zero curve and ranks off-the-run Treasuries by z-spread to screen rich/cheap issues (**+ = cheap, − = rich**). Data comes from TreasuryDirect FedInvest on 11/05/2026 and includes end-of-day prices for nominal bills, notes and bonds. On-the-runs are flagged from Treasury's issuance calendar as they are not provided by FedInvest.")

    # Linkedin
    st.markdown('#### Personal Information')
    linkedin_url = 'https://www.linkedin.com/in/michaelwojciechowski93'
    st.markdown(
        f'<a href="{linkedin_url}" target="_blank" style="text-decoration: none; color: inherit;"><img src="https://cdn-icons-png.flaticon.com/512/174/174857.png" width="25" height="25" style="vertical-align: middle; margin-right: 10px;">Michael Wojciechowski</a>',
        unsafe_allow_html=True)
    st.write("Github Repo: https://github.com/Wojo-mike43/SplineProject") 
