## Summary
Builds a US Treasury zero curve and ranks off-the-run Treasuries by z-spread to screen rich/cheap issues (**+ = cheap, − = rich**). Data comes from TreasuryDirect FedInvest on 11/05/2026 and includes end-of-day prices for nominal bills, notes and bonds. On-the-runs are flagged from Treasurys issuance calendar as they are not provided by FedInvest.
* Link to Data: https://www.treasurydirect.gov/GA-FI/FedInvest/todaySecurityPriceDetail. 

## Model Conventions
* T+1 settlement (11/06/2026); accrued interest and cash-flow times are measured from settlement.
* Bills: discount factor = price/100.
* Coupons: semiannual, actual/actual accrued, schedule built backward from maturity.
* Zero rates and z-spreads continuously compounded (act/365).

## Methodology
* Bootstrap a zero curve from on-the-run bills/ notes/bonds and a few off-the-runs past 10 years to anchor the long end of the curve.
* Fit a least-squares cubic B-spline to the zero rates (knots: 1, 2, 3, 5, 7, 10, 15, 20, 25y).
* Solve each off-the-runs z-spread (2y+ to maturity) with `brentq`.

## Key Takeaways
* Old 30-year bonds in the 10–13y sector trade rich (−9 to −14bp); 912810FT0 is −14.1bp versus +1.5bp for the note maturing the same day (91282CPZ8).
* Seasoned high coupons trade cheap: 912810FF0 +9.4bp.
* Low-coupon issues trade rich: 912828YS3 −4.3bp, 91282CBL4 −3.5bp, 912810SN9 −2.7bp.
* The 20-year sector trades slightly cheap (+1.5 to +2.3bp, 2042–44).

## Limitations / Next Steps
* Curve is built from on-the-runs, so spreads include the on-the-run liquidity premium.
* Curve-input bonds read ≈ 0 by construction and are excluded from the screen: 912810QE1, 912810TQ1, 912810UV8, 912810SE9, 912810TL2, 912810UU0
* Data is from a single day and does not yet include spread history.
* No repo or carry signal included due to data limitations.

## Run
* pip install -r requirements.txt
* streamlit run spline_vFinal.py
