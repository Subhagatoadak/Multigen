# Marketing Mix Modeling with Google Meridian + Multigen
## Real-World Use Cases & Implementation Playbook

---

## Executive Summary

Google Meridian is a Bayesian hierarchical MMM framework that produces full posterior distributions over media effectiveness — not point estimates. Combined with Multigen's durable multi-agent orchestration, epistemic transparency, and human-in-the-loop governance, it creates a production-grade MMM pipeline that can run weekly at scale, propagate uncertainty correctly, and pause for expert judgment at critical decision points.

This document covers six hard, real-world use cases where Meridian is the **right** choice — not a simpler regression-based tool — along with the exact Multigen agent architecture for each.

---

## Why Meridian + Multigen?

| MMM Requirement | Why Meridian | Why Multigen |
|---|---|---|
| 50+ geo markets simultaneously | Hierarchical Bayesian pooling | Fan-out agent per geo cluster |
| MCMC sampling takes 2–6 hours | HMC with NUTS, GPU-accelerated | Temporal durability — crash-safe long runs |
| Prior knowledge must be encoded | Native prior elicitation API | PriorElicitationAgent interviews domain experts |
| Results need human validation | Full posterior + Rhat diagnostics | Human approval gate before budget commitment |
| Weekly refresh cycle | Incremental model updating | Scheduled Kafka-triggered pipeline |
| Uncertainty must reach exec reports | Credible intervals on every estimate | Epistemic propagation through the DAG |
| Budget reallocation requires sign-off | Scenario planning API | Multi-level approval workflow |

---

## Use Case 1: Global FMCG Brand — Multi-Country TV + Digital Mix

### Business Context

A top-5 FMCG company (think P&G, Unilever, Nestlé) runs simultaneous campaigns for a household product across 12 countries. Each country has different channel mix (Germany: TV-heavy, UK: digital-first, Brazil: OOH + radio), different consumer response elasticities, and different competitive pressures. The CMO needs to know: **which country's budget is most efficiently deployed, and where should we move €50M next quarter?**

### Why Meridian (not Robyn or simple regression)

- 12 countries × 4 years weekly data = 2,496 observations per country: enough for Bayesian geo hierarchy
- Countries share information via hierarchical priors — small markets (Portugal) borrow strength from large (Germany)
- Reach & Frequency from DV360 is available — only Meridian uses it natively
- Regulatory and board-level decisions require credible intervals, not point estimates
- Prior knowledge from previous MMM runs (3 years of model history) can be encoded as informative priors

### Data Requirements

```
Weekly observations per country (4 years = 208 weeks):
- KPI: Revenue / Volume sold / Market share index
- TV: GRPs by week + 30s/15s/6s spot mix
- Digital: Impressions, Reach, Frequency (from DV360/YouTube)
- Paid Search: Clicks, Spend (Google Ads)
- OOH: Panel ratings by week
- Radio: GRPs (where applicable)
- Promotions: % stores on promotion, depth of discount
- Pricing: Average shelf price index
- Seasonality: Public holidays, local events, competitor activity proxy
- Macroeconomic: CPI, disposable income index per country
```

### Multigen Agent Pipeline

```yaml
# mmm_fmcg_global.yml
agents:
  - id: DataCollector
    type: EchoAgent
    config:
      role: input
      description: "Pulls weekly data from global data lake (Snowflake/BigQuery) across 12 markets"
      prompt: |
        Extract 208 weeks × 12 countries for: revenue, TV GRPs, digital reach/frequency,
        paid search spend, OOH ratings, pricing, promotions. Validate completeness.
        Flag any market with >5% missing weeks for remediation before proceeding.
      sub_agents: []

  - id: DataQualityAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Validates data integrity, detects anomalies, aligns temporal grain"
      prompt: |
        You are a senior data engineer specializing in MMM data pipelines.
        For each of the 12 country datasets:
        1. Check for structural breaks (COVID-19 March-June 2020, supply chain disruptions)
        2. Identify outlier weeks (spend > 3σ from mean — likely one-off events)
        3. Verify geo-grain consistency (national vs. regional aggregation)
        4. Confirm all channels share the same fiscal week definition
        5. Compute data completeness score (0-100) per country
        Flag countries scoring below 80 for manual remediation.
        Output: validated dataset + data quality report with per-country scores.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: PriorElicitationAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Generates Bayesian priors from industry benchmarks and historical MMM results"
      prompt: |
        You are a Bayesian statistician with deep FMCG MMM expertise.
        Construct informative priors for each media channel × country combination:

        TV:
          - ROI prior: LogNormal(mean=1.8, sigma=0.6) — FMCG TV typical range 0.8–4.2x
          - Adstock decay α: Beta(2, 5) — TV effects decay over 3–6 weeks
          - Hill slope: LogNormal(0, 0.5) — mild saturation for mature brands

        Digital (Reach × Frequency):
          - ROI prior: LogNormal(mean=2.2, sigma=0.5) — digital skews higher ROI
          - Adstock decay α: Beta(3, 7) — digital decays faster (1–2 weeks)
          - Hill ec: LogNormal(-0.5, 0.4) — faster saturation at high frequency

        Paid Search:
          - ROI prior: LogNormal(mean=3.1, sigma=0.4) — branded search high ROI
          - Adstock decay α: Beta(4, 8) — near-instantaneous effect

        OOH:
          - ROI prior: LogNormal(mean=1.2, sigma=0.7) — high uncertainty
          - Adstock decay α: Beta(2, 4) — moderate lag (2–4 weeks)

        Apply country-level scaling factors from previous model run results if available.
        Output: ModelSpec JSON with all priors encoded.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: HumanPriorReview
    type: EchoAgent
    config:
      role: assistant
      description: "Pauses workflow for media science team to validate priors before MCMC run"
      prompt: |
        Present prior specifications to the media science team for review.
        Required sign-off before MCMC sampling begins.
        Reviewers must confirm: ROI ranges are directionally correct, adstock
        parameters align with brand purchase cycle (repurchase every 2-3 weeks),
        and saturation parameters reflect current market penetration.
      sub_agents: []

  - id: MeridianFittingAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Executes Meridian MCMC sampling across all 12 markets"
      prompt: |
        Execute the following Meridian model fitting pipeline:

        ```python
        import meridian
        from meridian.model import Meridian
        from meridian import InputData, ModelSpec

        # Initialize with hierarchical geo model
        mmm = Meridian(
            input_data=InputData.from_dataframe(
                df=validated_data,
                kpi_col='revenue',
                media_cols=['tv_grp', 'digital_reach', 'digital_freq',
                            'paid_search_spend', 'ooh_grp'],
                control_cols=['pricing_index', 'promo_depth', 'cpi', 'holiday'],
                geo_col='country',
                time_col='fiscal_week'
            ),
            model_spec=elicited_model_spec
        )

        # MCMC sampling — chains sequenced to manage GPU memory across 12 geos
        mmm.sample_posterior(
            n_chains=4,
            n_adapt=1000,
            n_burnin=1000,
            n_keep=2000,
            seed=42
        )
        ```

        Monitor sampling: log chain progress every 500 draws.
        Expected duration: 3-5 hours on GPU instance.
        Output: inference_data object + sampling summary.
      params:
        config_path: ../config/default_config.yml
      sub_agents:
        - PriorElicitationAgent

  - id: DiagnosticsAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Validates MCMC convergence and model fit quality"
      prompt: |
        You are a Bayesian statistician performing model diagnostics.
        Execute comprehensive convergence and fit checks:

        CONVERGENCE:
        - Rhat: All parameters must have Rhat < 1.2 (flag any > 1.1 for review)
        - ESS (Effective Sample Size): Bulk ESS > 400 per parameter
        - HMC divergences: Must be < 0.5% of total draws
        - Trace plot visual inspection: chains should mix without trending

        FIT QUALITY:
        - National R²: Target > 0.85
        - Geo-level wMAPE: Target < 15% per country
        - Posterior predictive check: P(observed ≈ simulated) > 0.80
        - Prior-posterior comparison: Document which channels are data-informed
          vs. prior-dominated (data-informed = posterior significantly tighter than prior)

        PASS CRITERIA: Convergence + fit both pass → proceed to attribution.
        FAIL: If >2 countries fail convergence → flag for prior adjustment + re-run.
        Output: Diagnostics report with traffic-light status per country.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: AttributionAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Computes channel-level ROI, mROI, and contribution decomposition"
      prompt: |
        Extract full attribution analysis from the fitted model:

        ```python
        from meridian.analysis import Analyzer
        analyzer = Analyzer(mmm)

        # ROI with 90% credible intervals
        roi = analyzer.compute_roi(use_posterior=True)
        mroi = analyzer.compute_mroi(use_posterior=True)

        # Revenue decomposition (base + incremental per channel)
        contributions = analyzer.create_incremental_contribution(use_posterior=True)

        # Response curves (saturation visualization)
        for channel in media_channels:
            curves = analyzer.response_curves(channel=channel, use_posterior=True)
        ```

        For each country × channel, report:
        - ROI: mean + [5th, 95th] percentile credible interval
        - mROI: current marginal effectiveness
        - Revenue contribution %
        - Evidence of saturation (is current spend past the elbow?)
        - Probability ROI > 1.0 (P(profitable))

        Flag any channel × country where P(ROI < 1.0) > 25% — spending money ineffectively.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: BudgetOptimizer
    type: LLMAgent
    config:
      role: assistant
      description: "Generates optimal budget reallocation recommendations"
      prompt: |
        Run budget optimization scenarios for the global media plan:

        ```python
        from meridian.analysis.optimizer import BudgetOptimizer
        optimizer = BudgetOptimizer(mmm)

        # Scenario 1: Maintain total budget, maximize revenue
        scenario_flat = optimizer.optimize(
            fixed_budget=True,
            budget=current_total_budget,
            spend_constraint_lower={ch: 0.7 for ch in channels},  # max -30%
            spend_constraint_upper={ch: 1.5 for ch in channels},  # max +50%
            use_posterior=True
        )

        # Scenario 2: Target mROI ≥ 2.0 (aggressive efficiency)
        scenario_efficient = optimizer.optimize(
            fixed_budget=False,
            target_mroi=2.0,
            use_posterior=True
        )

        # Scenario 3: +10% incremental budget — where does it go?
        scenario_growth = optimizer.optimize(
            fixed_budget=True,
            budget=current_total_budget * 1.10,
            use_posterior=True
        )
        ```

        For each scenario, output:
        - Recommended spend per channel × country (vs. current)
        - Expected revenue lift with 80% credible interval
        - Number of countries where TV should be cut (past saturation)
        - Countries where digital reach should be increased
        - €-value of reallocation opportunity (pessimistic / central / optimistic)
      params:
        config_path: ../config/default_config.yml
      sub_agents:
        - AttributionAgent

  - id: HumanBudgetApproval
    type: EchoAgent
    config:
      role: assistant
      description: "CMO and Finance sign-off gate before budget recommendations go to agencies"
      prompt: |
        Present three budget scenarios to CMO and CFO for approval.
        Required: written sign-off on chosen scenario before recommendations
        are transmitted to media agencies.
        Capture: approved scenario ID, any country-level overrides (e.g., TV
        minimum floors for brand equity markets), and approval timestamp.
      sub_agents: []

  - id: ReportingAgent
    type: EchoAgent
    config:
      role: output
      description: "Generates executive MMM report and agency media plan"
      prompt: |
        Compile final deliverables:
        1. Executive summary (2 pages): top insights, reallocation €-value, risk/opportunity
        2. Country-level attribution cards (12 pages): waterfall charts, ROI table
        3. Response curve deck: saturation status per channel × country
        4. Agency media plan: updated GRP/impression targets by channel × country × week
        5. Model metadata: Rhat summary, data quality scores, prior vs posterior summary
        6. Refresh schedule: Next model run date, data requirements for next cycle
      sub_agents: []

links:
  - from: DataCollector
    to: DataQualityAgent
  - from: DataQualityAgent
    to: PriorElicitationAgent
  - from: PriorElicitationAgent
    to: HumanPriorReview
  - from: HumanPriorReview
    to: MeridianFittingAgent
  - from: MeridianFittingAgent
    to: DiagnosticsAgent
  - from: DiagnosticsAgent
    to: AttributionAgent
  - from: AttributionAgent
    to: BudgetOptimizer
  - from: BudgetOptimizer
    to: HumanBudgetApproval
  - from: HumanBudgetApproval
    to: ReportingAgent
```

### Business Output

- **€38–65M annual revenue uplift** from reallocation (typical 8–15% efficiency gain)
- Breakdown: Digital reach expanded in UK, DE, FR; TV cut in saturated markets (IT, ES)
- Confidence: P(revenue lift > €30M) = 0.91 with Bayesian credible intervals

---

## Use Case 2: Telecom Operator — Subscriber Acquisition Mix

### Business Context

A tier-1 telecom (Deutsche Telekom, AT&T, Vodafone scale) runs simultaneous acquisition campaigns across 5G mobile, home broadband, and bundled packages. The challenge: **subscriber acquisition involves a consideration-purchase funnel spanning 6–10 weeks**, retail store visits are offline, and competitor promotions create non-stationarity that breaks simple regression. The question: **what drives net subscriber additions, and which channel should we increase to hit Q3 subscriber targets?**

### Why Meridian (not Robyn)

- 6–10 week purchase consideration cycle requires long adstock lags (max_lag=13)
- Geographic variation is huge: urban markets (NYC, London) behave differently from rural
- Reach & Frequency from YouTube/Display is a core channel — only Meridian models it correctly
- Competitive offer launches create structural breaks that Bayesian models handle via robust priors

### Multigen Agent Pipeline

```yaml
# mmm_telecom_subscriber.yml
agents:
  - id: SubscriberDataIngestion
    type: EchoAgent
    config:
      role: input
      description: "Pulls subscriber net adds, churn, ARPU, and marketing spend from BSS/OSS"
      prompt: |
        Extract 3 years weekly data by DMA (50 markets):
        - KPI: Net subscriber additions (gross adds - churn) per product line
        - TV: GRPs × creative type (product-feature, brand, retention)
        - YouTube: Reach, frequency, TrueView vs. bumper split
        - Paid Search: Brand + category keywords, spend, impressions
        - Display/Programmatic: Reach, frequency (DV360)
        - OOH: Panel ratings by DMA
        - Retail: Promotions (handset subsidies, plan discounts, BOGOF)
        - Competitive: Competitor promo events (binary flag, scrape from monitoring)
        - Macro: 5G network coverage % by DMA, competitor network quality index
      sub_agents: []

  - id: FunnelDecompositionAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Separates awareness, consideration, and conversion effects by channel"
      prompt: |
        Telecom subscriber acquisition involves a multi-stage funnel:
        Awareness (TV, OOH, YouTube) → Consideration (Search, Website) → Conversion (Retail, Telesales)

        Segment channels by funnel stage:
        AWARENESS: TV GRPs, OOH, YouTube (brand reach)
        CONSIDERATION: Paid search (category), Display retargeting, Direct mail
        CONVERSION: Paid search (branded), Telesales contacts, Retail promos, Handset subsidies

        For MMM purposes, define:
        - Awareness channels: Long adstock (α ≈ 0.7, lag = 8–13 weeks)
        - Consideration channels: Medium adstock (α ≈ 0.5, lag = 4–8 weeks)
        - Conversion channels: Short adstock (α ≈ 0.2, lag = 1–3 weeks)

        Justify adstock parameterization based on network contract length
        (customers don't cancel a 24-month contract immediately).
        Output: Funnel-annotated channel taxonomy + adstock prior justification.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: CompetitiveAdjustmentAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Encodes competitive interference as control variables"
      prompt: |
        Competitive promo events suppress own-brand acquisition conversions.
        For each competitor promo event (binary flag by week × DMA):
        1. Create a 4-week distributed lag variable (competitor effect decays)
        2. Compute competitive intensity index: sum of competitor GRPs in same DMA/week
        3. Encode as control variables in Meridian ModelSpec (not as media channels)

        This prevents the MMM from attributing competitor-driven subscriber dips
        to a falsely negative media effect in own channels.
        Output: competitive_control_matrix DataFrame + encoded control spec.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: MeridianTelecomFit
    type: LLMAgent
    config:
      role: assistant
      description: "Fits hierarchical Meridian model with 50-DMA geo structure"
      prompt: |
        Run Meridian with hierarchical geo structure across 50 DMAs:

        Key model choices:
        - Geo hierarchy: Urban DMAs (top-15) vs. Suburban vs. Rural groups
        - Reach & Frequency treatment: YouTube modeled as R&F (not impressions)
        - max_lag = 13 (3-month purchase window for telecom)
        - Trend: Quarterly knots capturing 5G rollout coverage growth
        - Seasonality: Monthly + back-to-school + holiday peaks

        sample_posterior(n_chains=4, n_keep=2000) — expect 4-6 hours on GPU
        Monitor convergence carefully: telecom models often need wider priors
        on competitive control coefficients.
        Output: fitted model + posterior inference_data.
      params:
        config_path: ../config/default_config.yml
      sub_agents:
        - FunnelDecompositionAgent
        - CompetitiveAdjustmentAgent

  - id: AcquisitionAttributionAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Computes cost-per-acquisition (CPA) by channel with uncertainty"
      prompt: |
        Compute subscriber acquisition metrics from posterior:

        For each channel × DMA tier:
        - ROI in subscriber terms: Net adds per $1000 spend
        - CPA: $ per net subscriber addition (mean + 90% CI)
        - mROI: Marginal net adds from next $100K spend increase
        - Saturation status: Is current spend past diminishing returns elbow?
        - Funnel efficiency: Awareness-to-conversion ratio implied by model

        Critical business metric: CPA by channel vs. customer lifetime value (CLV)
        Flag any channel where CPA > 0.4 × CLV as value-destroying.
        Flag any channel where mROI > 1.5 × current average as under-invested.

        Output: CPA waterfall by channel, DMA-tier efficiency heatmap.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: Q3TargetSimulator
    type: LLMAgent
    config:
      role: assistant
      description: "Simulates required spend to hit Q3 subscriber target with confidence bands"
      prompt: |
        The business has a Q3 target: +185,000 net subscriber additions.
        Using the fitted model, simulate: what budget is required to achieve
        this target with 80% probability?

        Run optimizer scenarios:
        1. Base: Current budget allocation — what subscriber count at 80% confidence?
        2. Optimized: Best allocation of current budget — subscriber count improvement?
        3. Required: What total budget needed to hit 185K at P=0.80?

        For scenario 3, generate the spend-by-channel breakdown and flag
        any single channel that would require > 40% share (concentration risk).
        Output: Subscriber forecast confidence intervals + required budget table.
      params:
        config_path: ../config/default_config.yml
      sub_agents:
        - AcquisitionAttributionAgent

  - id: TelecomReport
    type: EchoAgent
    config:
      role: output
      description: "Q3 media plan recommendation with subscriber forecast"
      prompt: |
        Deliver: channel budget recommendations, Q3 forecast with 80% CI,
        CPA benchmark table, DMA-level opportunity map, and top 3 risks.
      sub_agents: []

links:
  - from: SubscriberDataIngestion
    to: FunnelDecompositionAgent
  - from: SubscriberDataIngestion
    to: CompetitiveAdjustmentAgent
  - from: FunnelDecompositionAgent
    to: MeridianTelecomFit
  - from: CompetitiveAdjustmentAgent
    to: MeridianTelecomFit
  - from: MeridianTelecomFit
    to: AcquisitionAttributionAgent
  - from: AcquisitionAttributionAgent
    to: Q3TargetSimulator
  - from: Q3TargetSimulator
    to: TelecomReport
```

---

## Use Case 3: Quick Service Restaurant — Transaction Volume Attribution

### Business Context

A QSR chain with 8,000 locations (McDonald's, Burger King, Yum! Brands scale) needs to measure which marketing activities drive **incremental restaurant transactions** — a hard metric because many QSR transactions are habitual (no marketing needed) and delivery platform growth (DoorDash, Uber Eats) is cannibalizing dine-in. The challenge: decompose **TV national spots, local radio, loyalty app pushes, delivery platform promos, and in-store offers** — where base habits are 80% of traffic and incremental marketing contribution is 20%.

### Why Meridian (not Robyn)

- **8,000 locations grouped into 210 DMAs** — perfect for hierarchical geo model
- Loyalty app engagement creates reach & frequency data consumable by Meridian
- National TV effects must decay correctly — QSR purchase cycle is 3–5 days (very short adstock), not weeks
- P(ROI > 1) is the decision metric — boards require Bayesian credible intervals, not R²

### Multigen Agent Pipeline

```yaml
# mmm_qsr_transactions.yml
agents:
  - id: QSRDataPipeline
    type: EchoAgent
    config:
      role: input
      description: "Pulls POS transaction data, marketing spend, and loyalty app data by DMA"
      prompt: |
        Extract daily aggregated to weekly, 3 years, 210 DMAs:
        - KPI: Total transaction count + delivery vs. dine-in split
        - National TV: GRPs by creative (value, new product, brand)
        - Local Radio: GRPs by DMA
        - Digital: YouTube reach/frequency, Paid social (Meta, TikTok) impressions/reach
        - Loyalty App: Weekly active users, push notification reach, redemption rate
        - Delivery Platforms: DoorDash/UberEats promo weeks (binary + discount depth)
        - Promotions: $1 menu, BOGO, limited-time offers (LTO) by week
        - Pricing: Weighted average check size index (inflation control)
        - Competitive: Competitor location openings near DMA, competitor LTO events
      sub_agents: []

  - id: BaseSalesDecomposer
    type: LLMAgent
    config:
      role: assistant
      description: "Identifies habitual base vs. marketing-driven incremental transactions"
      prompt: |
        QSR is unique: 75-85% of weekly transactions are habitual base traffic.
        Configure the Meridian model to correctly separate base from incremental:

        1. Trend modeling: Use weekly knots to capture base traffic growth/decline
           (includes population growth, store count changes, format evolution)
        2. Seasonality: Weekly + monthly + holiday (Thanksgiving, Super Bowl are huge)
        3. Structural breaks: COVID-19 closures (encode as 0 weeks, not missing)
        4. Delivery platform effect: model DoorDash/Uber as their own channel
           with positive contribution but potential cannibalization of dine-in

        Short adstock for QSR: TV effects decay within 5–7 days (α ≈ 0.3–0.5)
        App push notifications: near-instantaneous effect (lag=0, α ≈ 0.1)
        Output: Base/incremental separation framework + ModelSpec adjustments.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: LoyaltyAppMMM
    type: LLMAgent
    config:
      role: assistant
      description: "Models loyalty app as reach/frequency channel using Meridian R&F API"
      prompt: |
        The loyalty app is a unique channel: it has genuine Reach (unique active users)
        and Frequency (push notifications sent per user per week).
        Model it as a Reach & Frequency channel in Meridian:

        Reach = weekly unique app users who received at least one push notification
        Frequency = average push notifications per user per week

        Apply Hill saturation to frequency (users become desensitized above 3 pushes/week)
        Apply geometric adstock with short lag (α=0.2, max_lag=3)

        This allows the model to separately estimate:
        - Effectiveness of reaching more users (reach effect)
        - Optimal push frequency (frequency saturation)
        Both are actionable for the CRM/app team.
        Output: R&F encoded channel spec for loyalty app.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: MeridianQSRFit
    type: LLMAgent
    config:
      role: assistant
      description: "Fits Meridian with DMA-level hierarchy across 210 QSR markets"
      prompt: |
        Fit hierarchical model with DMA clusters:
        - Urban (top-50 DMAs): Higher digital mix, delivery platform penetration
        - Suburban (100 DMAs): TV-dominant, loyalty app growing
        - Rural (60 DMAs): Radio-dominant, minimal delivery

        Prior for TV ROI: LogNormal(mean=1.4, sigma=0.5) — QSR TV ROI typically 0.6–3.0x
        Prior for Radio: LogNormal(mean=0.9, sigma=0.6) — lower, more uncertain
        Prior for Loyalty App: LogNormal(mean=2.8, sigma=0.4) — high ROI (already opted-in users)
        Prior for Delivery Promo: LogNormal(mean=1.1, sigma=0.8) — uncertain, commission drag

        sample_posterior(n_chains=4, n_keep=2000, seed=42)
        Output: fitted model + convergence summary.
      params:
        config_path: ../config/default_config.yml
      sub_agents:
        - BaseSalesDecomposer
        - LoyaltyAppMMM

  - id: MenuItemROIAgent
    type: LLMAgent
    config:
      role: assistant
      description: "Computes per-LTO campaign ROI and optimal promotional frequency"
      prompt: |
        LTOs (Limited Time Offers) are a key QSR mechanism. From the fitted model:

        1. Compute incremental transactions per LTO event (mean + CI)
        2. Estimate LTO advertising ROI: TV + digital spend supporting each LTO
        3. Identify: how many concurrent LTOs depress individual LTO lift?
           (LTO fatigue — consumer confusion when too many active simultaneously)
        4. Optimal LTO cadence: How many per quarter maximizes total transaction lift?
        5. Delivery platform promos: Net ROI after platform commission (typically 25-30%)
           — are delivery promos net-positive after commission?

        Flag: Any LTO with P(ROI < 1.0) > 30% — consider discontinuing.
        Output: LTO effectiveness ranking + cadence recommendation.
      params:
        config_path: ../config/default_config.yml
      sub_agents: []

  - id: DMAOpportunityMap
    type: LLMAgent
    config:
      role: assistant
      description: "Identifies under/over-invested DMAs for localized budget shifts"
      prompt: |
        Using geo-level posterior estimates, create a DMA opportunity map:

        For each of 210 DMAs, compute:
        - Current spend per transaction (efficiency index)
        - mROI for each channel in that DMA
        - Delta from national average mROI (over/under-invested signal)
        - Recommended spend change: +/- % by channel

        Segment DMAs into quadrants:
        Q1: High efficiency + under-invested → increase spend here
        Q2: High efficiency + over-invested → maintain, harvest returns
        Q3: Low efficiency + under-invested → investigate structural issues
        Q4: Low efficiency + over-invested → cut spend, reallocate to Q1

        Output: DMA quadrant map (CSV) + top-20 opportunity DMAs narrative.
      params:
        config_path: ../config/default_config.yml
      sub_agents:
        - MenuItemROIAgent

  - id: QSRExecutiveReport
    type: EchoAgent
    config:
      role: output
      description: "Transaction forecast, channel ROI table, DMA opportunity heat map"
      prompt: |
        Produce: transaction attribution waterfall (base vs. each channel),
        channel ROI league table with credible intervals, DMA opportunity map,
        LTO recommendation deck, and loyalty app optimization brief.
      sub_agents: []

links:
  - from: QSRDataPipeline
    to: BaseSalesDecomposer
  - from: QSRDataPipeline
    to: LoyaltyAppMMM
  - from: BaseSalesDecomposer
    to: MeridianQSRFit
  - from: LoyaltyAppMMM
    to: MeridianQSRFit
  - from: MeridianQSRFit
    to: MenuItemROIAgent
  - from: MenuItemROIAgent
    to: DMAOpportunityMap
  - from: DMAOpportunityMap
    to: QSRExecutiveReport
```

---

## Use Case 4: Pharmaceutical — HCP Detailing ROI vs. Rx Volume

### Business Context

A top-10 pharma company (Pfizer, AstraZeneca, Novartis scale) spends $1.2B annually promoting a portfolio of drugs to physicians via sales rep detailing, medical journal advertising, congresses, samples, and digital HCP platforms. The key question: **for a drug losing patent protection in 18 months, which HCP promotion channels should we sustain vs. wind down — and how does the ROI profile change by physician specialty and geography?**

### Why Meridian (not simpler tools)

- Prescription writing has an inherent lag (physicians take months to change prescribing habits) — long adstock essential
- Physician reach & frequency data from details/samples is available at rep level
- Bayesian priors can encode clinical trial results as informed starting points for efficacy beliefs
- Credible intervals are required for regulatory compliance documentation
- Hierarchical model captures specialty × territory interaction effects

### Multigen Agent Pipeline Key Agents

```yaml
agents:
  - id: RxDataIngestion         # IMS Health / IQVIA weekly Rx data by HCP segment
  - id: CallActivityNormalizer   # Normalize rep detailing as reach × frequency per physician
  - id: SpecialtyPriorAgent     # Different ROI priors per specialty (cardiologist vs. GP)
  - id: MeridianPharmaFit       # max_lag=26 (6-month Rx habit formation lag)
  - id: ChannelWearoutAgent     # Detect detailing fatigue (mROI declining over time)
  - id: LossOfExclusivitySim    # Simulate Rx trajectory post-patent cliff with current mix
  - id: PharmaReportAgent       # Field force optimization + digital shift recommendation
```

**Unique Meridian Advantage**: Physician detailing is literally a Reach × Frequency problem — doctors reached by reps × number of details per doctor. Meridian's R&F module handles this natively. No other open-source MMM tool does.

---

## Use Case 5: E-Commerce / DTC — Full-Funnel Performance Mix

### Business Context

A DTC brand (Warby Parker, Allbirds, Casper scale, $500M+ revenue) runs a performance marketing machine: Meta, Google SEM, TikTok, affiliates, email, influencers, and connected TV. The CFO is pressured to cut CAC by 20% while growing revenue 15%. The problem: **last-click attribution over-credits SEM, under-credits CTV and influencers**, and nobody knows the true incrementality of any channel. MMM provides the channel-agnostic truth.

### Why Meridian (not just MTA or last-click)

- Multi-touch attribution (MTA) requires deterministic user-level data — increasingly unavailable (iOS 14.5+, cookie deprecation)
- Reach & Frequency from Meta and DV360 directly feeds Meridian
- Weekly revenue × channel spend data is clean and available in real-time via API connectors
- Bayesian uncertainty propagation is critical — DTC CAC decisions affect inventory and headcount

### Multigen Agent Pipeline Key Agents

```yaml
agents:
  - id: RevenueDataPuller       # BigQuery warehouse: daily/weekly revenue by SKU/channel
  - id: AttributionReconciler   # Cross-reference last-click, MTA, MMM — identify discrepancy
  - id: IncrementalityTestIngestor  # Pull results from existing Meta/Google incrementality tests
                                    # Use as informative priors for Meridian
  - id: MeridianDTCFit          # Weekly model, national-only (small brand, one market)
  - id: CACAnomalyDetector      # Flag weeks where MMM-attributed CAC diverges >25% from GA
  - id: ChannelMixOptimizer     # Maximize revenue at target CAC constraint
  - id: WeeklyRefreshAgent      # Schedule weekly re-run as new data arrives
  - id: CMODashboardPublisher   # Publish MMM-reconciled CAC to exec dashboard
```

**Key Insight for DTC**: Run Meridian weekly with a rolling 2-year window. As new spend data arrives, the posterior updates. This creates a **living model** that continuously recalibrates channel weights as media costs change (CPM inflation, TikTok efficiency shifts, etc.).

---

## Use Case 6: Automotive OEM — Brand Spend vs. Dealer Traffic & Order Intake

### Business Context

A premium auto OEM (BMW, Mercedes, Audi level) spends €400M annually in marketing. The purchase funnel is extremely long: awareness → configuration → test drive → order → delivery can span 9–18 months for EVs. The question: **does national TV brand advertising actually convert to order intake 6–9 months later? How do we prove this to the board when the finance team wants to cut brand budgets and move everything to digital lead generation?**

### Why Meridian

- 9–18 month purchase cycle demands max_lag = 52 (1 year of weekly lags) — no tool other than Bayesian MMM handles this without overfitting
- Geo hierarchy by sales region (15 regions × country) captures dealer density effects
- Prior knowledge from brand tracking studies (awareness lift per GRP) can be encoded
- Credible intervals are essential — a point estimate ROI on a €400M budget is insufficient evidence for board-level decisions

### Multigen Agent Pipeline Key Agents

```yaml
agents:
  - id: DealerTrafficIngestion  # Weekly: website configurator sessions, dealer enquiries,
                                #         test drives booked, orders placed, deliveries
  - id: BrandTrackingIntegrator # Monthly brand metrics: awareness, consideration, intent
                                # (from Kantar/Ipsos tracker) → interpolate to weekly
  - id: PurchaseCyclePriorAgent # Set max_lag=52 for brand channels, max_lag=8 for lead-gen
  - id: MeridianAutoFit         # Full 3-year model, national + 15-region hierarchy
  - id: FunnelStageLinker       # Estimate awareness → consideration → test drive → order
                                # conversion rates from posterior
  - id: BrandVsPerformanceROI   # The key output: brand ROI on 12-18mo horizon vs.
                                # performance ROI on 4-week horizon — like-for-like comparison
  - id: BoardPresentationAgent  # Generate board-ready evidence deck with credible intervals
```

**Critical Output**: The board deck must answer — *"If we cut brand TV by €50M and move it to digital lead gen, what happens to order intake in 12 months?"*. Only Meridian can answer this with statistical rigor via its long-lag adstock + posterior predictive simulation.

---

## Architecture: Multigen MMM Platform

```
                    ┌─────────────────────────────────────────┐
                    │         DATA LAYER                       │
                    │  BigQuery / Snowflake / Redshift         │
                    │  Ad Platform APIs (Google, Meta, DV360)  │
                    │  POS / CRM / ERP systems                 │
                    └──────────────┬──────────────────────────┘
                                   │ Kafka trigger (new week data)
                    ┌──────────────▼──────────────────────────┐
                    │      MULTIGEN ORCHESTRATOR               │
                    │                                          │
                    │  DataIngestionAgent ──► DataQualityAgent │
                    │         │                     │          │
                    │         ▼                     ▼          │
                    │  PriorElicitationAgent  ◄─ EDAAgent      │
                    │         │                                 │
                    │         ▼                                 │
                    │  ┌─ HUMAN GATE: Prior Review ─┐          │
                    │  │  (Media Science sign-off)   │          │
                    │  └──────────────┬──────────────┘          │
                    │                 ▼                          │
                    │  MeridianFittingAgent (GPU, 3-6hrs)       │
                    │         │                                  │
                    │         ▼                                  │
                    │  DiagnosticsAgent (Rhat, ESS, MAPE)       │
                    │         │                                  │
                    │    PASS?│ ──NO──► PriorAdjustmentAgent    │
                    │    YES  │              │                   │
                    │         ▼              └──► MeridianFit   │
                    │  AttributionAgent (ROI, mROI, Contrib)    │
                    │         │                                  │
                    │         ▼                                  │
                    │  BudgetOptimizer (3 scenarios)            │
                    │         │                                  │
                    │  ┌─ HUMAN GATE: CMO/CFO Approval ─┐      │
                    │  │  (Budget scenario sign-off)     │      │
                    │  └──────────────┬──────────────────┘      │
                    │                 ▼                          │
                    │  ReportingAgent → Agency Briefs           │
                    └─────────────────────────────────────────-─┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │         SIMULATOR FRONTEND               │
                    │  Real-time agent activity feed           │
                    │  Convergence monitoring (Rhat live)      │
                    │  Budget scenario comparison              │
                    │  Human approval gates                    │
                    └─────────────────────────────────────────-┘
```

---

## Implementation Guide

### Step 1: Install Meridian in the Backend

```bash
# Add to agentic-simulator/backend/requirements.txt
google-meridian>=0.1.0
tensorflow>=2.15.0
tensorflow-probability>=0.23.0
arviz>=0.18.0
jax>=0.4.25
jaxlib>=0.4.25
```

### Step 2: Create a MeridianAgent Base Class

```python
# agentic-simulator/backend/agents/meridian_agent.py

from agents.base_agent import BaseAgent
import meridian
from meridian.model import Meridian
from meridian import InputData, ModelSpec
from meridian.analysis import Analyzer
from meridian.analysis.optimizer import BudgetOptimizer
import pandas as pd
import json

class MeridianFittingAgent(BaseAgent):
    """Executes Meridian MCMC sampling as a durable Temporal activity."""

    async def run(self, params: dict) -> dict:
        # Load data from params
        df = pd.DataFrame(params['data'])

        # Build InputData
        input_data = InputData.from_dataframe(
            df=df,
            kpi_col=params['kpi_col'],
            media_cols=params['media_cols'],
            control_cols=params.get('control_cols', []),
            geo_col=params.get('geo_col'),
            time_col=params['time_col']
        )

        # Load ModelSpec from prior elicitation output
        model_spec = ModelSpec(**params['model_spec'])

        # Initialize and fit
        mmm = Meridian(input_data=input_data, model_spec=model_spec)
        mmm.sample_posterior(
            n_chains=params.get('n_chains', 4),
            n_keep=params.get('n_keep', 2000),
            seed=params.get('seed', 42)
        )

        # Extract key results
        analyzer = Analyzer(mmm)
        roi = analyzer.compute_roi(use_posterior=True)
        mroi = analyzer.compute_mroi(use_posterior=True)

        # Convergence summary
        rhat_max = float(mmm.inference_data.posterior.coords.to_dict()
                         .get('rhat_max', 99))

        return {
            'status': 'fitted',
            'roi': roi,
            'mroi': mroi,
            'rhat_max': rhat_max,
            'convergence_passed': rhat_max < 1.2,
            'confidence': 0.90 if rhat_max < 1.1 else 0.70,
            'model_artifact_path': self._save_model(mmm, params['run_id'])
        }

    def _save_model(self, mmm, run_id: str) -> str:
        path = f"/artifacts/mmm_{run_id}.pkl"
        mmm.inference_data.to_netcdf(path)
        return path


class MeridianDiagnosticsAgent(BaseAgent):
    async def run(self, params: dict) -> dict:
        inference_data = self._load_inference_data(params['model_artifact_path'])
        rhat_values = {
            str(k): float(v.max())
            for k, v in inference_data.posterior.data_vars.items()
        }
        max_rhat = max(rhat_values.values())
        divergences = int(
            inference_data.sample_stats.diverging.values.sum()
        )
        return {
            'convergence_passed': max_rhat < 1.2,
            'max_rhat': max_rhat,
            'rhat_by_param': rhat_values,
            'divergences': divergences,
            'recommendation': 'proceed' if max_rhat < 1.2 else 'adjust_priors',
            'confidence': 0.95 if max_rhat < 1.05 else 0.75
        }


class MeridianOptimizerAgent(BaseAgent):
    async def run(self, params: dict) -> dict:
        mmm = self._load_model(params['model_artifact_path'])
        optimizer = BudgetOptimizer(mmm)

        results = {}
        for scenario in params['scenarios']:
            result = optimizer.optimize(
                fixed_budget=scenario.get('fixed_budget', True),
                budget=scenario.get('budget'),
                target_mroi=scenario.get('target_mroi'),
                spend_constraint_lower=scenario.get('lower_bounds', {}),
                spend_constraint_upper=scenario.get('upper_bounds', {}),
                use_posterior=True
            )
            results[scenario['name']] = {
                'optimal_spend': result.optimal_spend.to_dict(),
                'expected_kpi': float(result.expected_kpi_mean),
                'kpi_ci_low': float(result.expected_kpi_quantile(0.10)),
                'kpi_ci_high': float(result.expected_kpi_quantile(0.90)),
                'vs_current_pct': result.pct_improvement
            }

        return {
            'scenarios': results,
            'recommended_scenario': max(results, key=lambda k: results[k]['expected_kpi']),
            'confidence': 0.88
        }
```

### Step 3: Load the YAML and Run via Multigen

```bash
# Via Multigen CLI
multigen run --workflow mmm_fmcg_global.yml \
             --params '{"run_id": "fmcg_2026_q1", "n_chains": 4}'

# Monitor in Simulator UI
open http://localhost:5173  # → Workflows tab shows live MCMC progress
```

### Step 4: Human Gate Configuration

The `HumanPriorReview` and `HumanBudgetApproval` agents trigger Multigen's approval workflow:

```bash
# Check pending approvals
GET /workflows/{id}/pending-approvals

# Approve (media science team)
POST /workflows/{id}/approve-agent
{
  "agent_id": "HumanPriorReview",
  "reviewer": "dr.chen@brand.com",
  "notes": "TV ROI prior confirmed against 2024 model run"
}
```

---

## When is Meridian the Right Choice?

| Signal | Recommendation |
|---|---|
| You have geo-level data (DMAs/regions) and 2+ years of history | **Use Meridian** |
| You have YouTube/Display Reach & Frequency data | **Use Meridian** — no other OSS MMM handles R&F |
| You need credible intervals for board / regulatory use | **Use Meridian** |
| Purchase cycle > 4 weeks (telecom, auto, pharma) | **Use Meridian** (long adstock support) |
| You have previous model results or experiment data to encode | **Use Meridian** (prior integration) |
| You need a quick-and-dirty 2-week prototype | Use Robyn (faster to set up) |
| You're a small brand, single market, tight budget | Use Robyn or PyMC-Marketing |
| You need real-time attribution (daily) | MMM is wrong tool — use MTA |

---

*Document version: March 2026 | Multigen + Google Meridian 0.1.x*
