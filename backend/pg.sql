BEGIN;

INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Generating static SQL
INFO  [alembic.runtime.migration] Will assume transactional DDL.
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

INFO  [alembic.runtime.migration] Running upgrade  -> 0001_initial, initial schema
-- Running upgrade  -> 0001_initial

CREATE TABLE ai_prompts (
    id SERIAL NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    version VARCHAR(16) NOT NULL, 
    task_type VARCHAR(48) NOT NULL, 
    system_prompt TEXT NOT NULL, 
    user_template TEXT NOT NULL, 
    output_schema_json JSON, 
    capability_tier VARCHAR(16) NOT NULL, 
    is_active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_ai_prompt UNIQUE (name, version)
);

CREATE TABLE ai_providers (
    id SERIAL NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    provider_type VARCHAR(32) NOT NULL, 
    base_url VARCHAR(512) NOT NULL, 
    api_key_encrypted TEXT, 
    default_model VARCHAR(128), 
    is_active BOOLEAN NOT NULL, 
    daily_budget_usd NUMERIC(12, 4) NOT NULL, 
    settings_json JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (name)
);

CREATE TABLE assets (
    id SERIAL NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    display_name VARCHAR(128), 
    asset_class VARCHAR(16) NOT NULL, 
    currency VARCHAR(8) NOT NULL, 
    exchange VARCHAR(64), 
    is_active BOOLEAN NOT NULL, 
    metadata_json JSON, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_assets_asset_class CHECK (asset_class in ('stock','etf','crypto','index','future')), 
    UNIQUE (symbol)
);

CREATE TABLE audit_logs (
    id SERIAL NOT NULL, 
    event_type VARCHAR(64) NOT NULL, 
    actor VARCHAR(128) NOT NULL, 
    entity_type VARCHAR(48) NOT NULL, 
    entity_id VARCHAR(48) NOT NULL, 
    action VARCHAR(32) NOT NULL, 
    payload_json JSON, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_audit_logs_created ON audit_logs (created_at);

CREATE INDEX ix_audit_logs_entity ON audit_logs (entity_type, entity_id);

CREATE TABLE features (
    id SERIAL NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    feature_type VARCHAR(32) NOT NULL, 
    feature_version VARCHAR(16) NOT NULL, 
    description TEXT, 
    inputs_json JSON NOT NULL, 
    params_json JSON NOT NULL, 
    is_deterministic BOOLEAN NOT NULL, 
    lookahead_safe BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (name)
);

CREATE TABLE github_sources (
    id SERIAL NOT NULL, 
    repository_url VARCHAR(512) NOT NULL, 
    default_branch VARCHAR(128) NOT NULL, 
    current_commit VARCHAR(64), 
    license VARCHAR(128), 
    author VARCHAR(128), 
    is_watched BOOLEAN NOT NULL, 
    last_checked_at TIMESTAMP WITH TIME ZONE, 
    last_import_status VARCHAR(32), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE jobs (
    id SERIAL NOT NULL, 
    job_type VARCHAR(48) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    idempotency_key VARCHAR(128), 
    payload_json JSON NOT NULL, 
    result_json JSON, 
    error_message TEXT, 
    progress INTEGER NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    retry_count INTEGER NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (idempotency_key)
);

CREATE TABLE market_data_sources (
    id SERIAL NOT NULL, 
    name VARCHAR(64) NOT NULL, 
    provider_type VARCHAR(32) NOT NULL, 
    base_url VARCHAR(512) NOT NULL, 
    api_key_encrypted TEXT, 
    rate_limit_per_minute INTEGER, 
    is_active BOOLEAN NOT NULL, 
    metadata_json JSON, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (name)
);

CREATE TABLE strategies (
    id SERIAL NOT NULL, 
    name VARCHAR(128) NOT NULL, 
    slug VARCHAR(128) NOT NULL, 
    description TEXT, 
    source_type VARCHAR(24) NOT NULL, 
    source_url VARCHAR(512), 
    license VARCHAR(128), 
    author VARCHAR(128), 
    status VARCHAR(24) NOT NULL, 
    lifecycle VARCHAR(24) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (slug)
);

CREATE TABLE system_settings (
    id SERIAL NOT NULL, 
    key VARCHAR(96) NOT NULL, 
    value_json JSON, 
    description TEXT, 
    is_secret BOOLEAN NOT NULL, 
    updated_by VARCHAR(128) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (key)
);

CREATE TABLE ai_models (
    id SERIAL NOT NULL, 
    provider_id INTEGER NOT NULL, 
    model_name VARCHAR(128) NOT NULL, 
    capability_tier VARCHAR(16) NOT NULL, 
    context_length INTEGER, 
    supports_structured_output BOOLEAN NOT NULL, 
    input_cost_per_mtok NUMERIC(12, 6) NOT NULL, 
    output_cost_per_mtok NUMERIC(12, 6) NOT NULL, 
    is_active BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(provider_id) REFERENCES ai_providers (id) ON DELETE CASCADE, 
    CONSTRAINT uq_ai_model UNIQUE (provider_id, model_name)
);

CREATE TABLE github_snapshots (
    id SERIAL NOT NULL, 
    source_id INTEGER NOT NULL, 
    commit VARCHAR(64) NOT NULL, 
    manifest_json JSON, 
    extraction_json JSON, 
    content_hash VARCHAR(64) NOT NULL, 
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(source_id) REFERENCES github_sources (id) ON DELETE CASCADE, 
    CONSTRAINT uq_github_snapshot UNIQUE (source_id, commit)
);

CREATE TABLE job_logs (
    id SERIAL NOT NULL, 
    job_id INTEGER NOT NULL, 
    level VARCHAR(16) NOT NULL, 
    message TEXT NOT NULL, 
    context_json JSON, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE market_data (
    id SERIAL NOT NULL, 
    asset_id INTEGER NOT NULL, 
    timeframe VARCHAR(8) NOT NULL, 
    source_id INTEGER NOT NULL, 
    timezone VARCHAR(64) NOT NULL, 
    adjusted BOOLEAN NOT NULL, 
    dataset_version VARCHAR(32) NOT NULL, 
    series_start TIMESTAMP WITH TIME ZONE, 
    series_end TIMESTAMP WITH TIME ZONE, 
    last_sync_at TIMESTAMP WITH TIME ZONE, 
    quality_status VARCHAR(16) NOT NULL, 
    content_hash VARCHAR(64), 
    is_archived BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(asset_id) REFERENCES assets (id) ON DELETE CASCADE, 
    FOREIGN KEY(source_id) REFERENCES market_data_sources (id), 
    CONSTRAINT uq_market_data_series UNIQUE (asset_id, timeframe, source_id, dataset_version)
);

CREATE INDEX ix_market_data_asset_timeframe ON market_data (asset_id, timeframe);

CREATE TABLE paper_accounts (
    id SERIAL NOT NULL, 
    name VARCHAR(128) NOT NULL, 
    strategy_id INTEGER, 
    base_currency VARCHAR(8) NOT NULL, 
    initial_cash NUMERIC(24, 8) NOT NULL, 
    cash NUMERIC(24, 8) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    settings_json JSON NOT NULL, 
    reset_count INTEGER NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(strategy_id) REFERENCES strategies (id)
);

CREATE TABLE strategy_versions (
    id SERIAL NOT NULL, 
    strategy_id INTEGER NOT NULL, 
    version VARCHAR(32) NOT NULL, 
    schema_version VARCHAR(8) NOT NULL, 
    dsl_json JSON NOT NULL, 
    source_commit VARCHAR(64), 
    source_url VARCHAR(512), 
    prompt_version VARCHAR(32), 
    evidence_json JSON, 
    immutable_hash VARCHAR(64) NOT NULL, 
    is_current BOOLEAN NOT NULL, 
    validation_status VARCHAR(16) NOT NULL, 
    validation_errors JSON, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(strategy_id) REFERENCES strategies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_strategy_version UNIQUE (strategy_id, version)
);

CREATE TABLE ai_tasks (
    id SERIAL NOT NULL, 
    task_type VARCHAR(48) NOT NULL, 
    provider_id INTEGER, 
    model_id INTEGER, 
    prompt_name VARCHAR(64) NOT NULL, 
    prompt_version VARCHAR(16) NOT NULL, 
    input_hash VARCHAR(64) NOT NULL, 
    input_json JSON, 
    output_json JSON, 
    token_usage_json JSON, 
    cost_usd NUMERIC(12, 6) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    error_message TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(model_id) REFERENCES ai_models (id), 
    FOREIGN KEY(provider_id) REFERENCES ai_providers (id)
);

CREATE INDEX ix_ai_tasks_type_status ON ai_tasks (task_type, status);

CREATE TABLE ai_usage (
    id SERIAL NOT NULL, 
    usage_date DATE NOT NULL, 
    provider_id INTEGER, 
    model_id INTEGER, 
    task_type VARCHAR(48) NOT NULL, 
    call_count INTEGER NOT NULL, 
    total_tokens INTEGER NOT NULL, 
    total_cost_usd NUMERIC(12, 6) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(model_id) REFERENCES ai_models (id), 
    FOREIGN KEY(provider_id) REFERENCES ai_providers (id), 
    CONSTRAINT uq_ai_usage UNIQUE (usage_date, provider_id, model_id, task_type)
);

CREATE TABLE feature_snapshots (
    id SERIAL NOT NULL, 
    series_id INTEGER NOT NULL, 
    bar_timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
    feature_version VARCHAR(16) NOT NULL, 
    values_json JSON NOT NULL, 
    input_hash VARCHAR(64) NOT NULL, 
    available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(series_id) REFERENCES market_data (id) ON DELETE CASCADE, 
    CONSTRAINT uq_feature_snapshot UNIQUE (series_id, bar_timestamp, feature_version)
);

CREATE TABLE market_data_bars (
    series_id INTEGER NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
    open NUMERIC(20, 8) NOT NULL, 
    high NUMERIC(20, 8) NOT NULL, 
    low NUMERIC(20, 8) NOT NULL, 
    close NUMERIC(20, 8) NOT NULL, 
    volume NUMERIC(28, 8) NOT NULL, 
    amount NUMERIC(28, 8), 
    is_closed BOOLEAN NOT NULL, 
    source_hash VARCHAR(64) NOT NULL, 
    PRIMARY KEY (series_id, timestamp), 
    CONSTRAINT ck_bars_high_ge_close CHECK (high >= close), 
    CONSTRAINT ck_bars_high_ge_low CHECK (high >= low), 
    CONSTRAINT ck_bars_high_ge_open CHECK (high >= open), 
    CONSTRAINT ck_bars_low_le_close CHECK (low <= close), 
    CONSTRAINT ck_bars_low_le_open CHECK (low <= open), 
    FOREIGN KEY(series_id) REFERENCES market_data (id) ON DELETE CASCADE
);

CREATE INDEX ix_market_data_bars_ts ON market_data_bars (timestamp);

CREATE TABLE paper_positions (
    id SERIAL NOT NULL, 
    account_id INTEGER NOT NULL, 
    asset_id INTEGER NOT NULL, 
    quantity NUMERIC(24, 10) NOT NULL, 
    avg_cost NUMERIC(20, 8) NOT NULL, 
    realized_pnl NUMERIC(24, 8) NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(account_id) REFERENCES paper_accounts (id) ON DELETE CASCADE, 
    FOREIGN KEY(asset_id) REFERENCES assets (id), 
    CONSTRAINT uq_paper_position UNIQUE (account_id, asset_id)
);

CREATE TABLE strategy_parameters (
    id SERIAL NOT NULL, 
    strategy_version_id INTEGER NOT NULL, 
    parameters_json JSON NOT NULL, 
    description TEXT, 
    is_default BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions (id) ON DELETE CASCADE
);

CREATE TABLE backtest_runs (
    id SERIAL NOT NULL, 
    strategy_version_id INTEGER NOT NULL, 
    dataset_version_id INTEGER NOT NULL, 
    parameters_id INTEGER, 
    engine_version VARCHAR(16) NOT NULL, 
    feature_version VARCHAR(16) NOT NULL, 
    parameters_json JSON NOT NULL, 
    execution_model_json JSON NOT NULL, 
    dataset_hash VARCHAR(64) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    error_message TEXT, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(dataset_version_id) REFERENCES market_data (id), 
    FOREIGN KEY(parameters_id) REFERENCES strategy_parameters (id), 
    FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions (id)
);

CREATE INDEX ix_backtest_runs_status ON backtest_runs (status);

CREATE INDEX ix_backtest_runs_strategy ON backtest_runs (strategy_version_id);

CREATE TABLE signals (
    id SERIAL NOT NULL, 
    strategy_version_id INTEGER NOT NULL, 
    asset_id INTEGER NOT NULL, 
    timeframe VARCHAR(8) NOT NULL, 
    bar_timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
    state VARCHAR(16) NOT NULL, 
    direction VARCHAR(8) NOT NULL, 
    price_reference NUMERIC(20, 8), 
    stop_reference NUMERIC(20, 8), 
    target_reference NUMERIC(20, 8), 
    triggered_rules_json JSON NOT NULL, 
    feature_snapshot_hash VARCHAR(64) NOT NULL, 
    data_source VARCHAR(64) NOT NULL, 
    portfolio_context_json JSON, 
    explanation_json JSON, 
    ai_task_id INTEGER, 
    status VARCHAR(16) NOT NULL, 
    generated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    acknowledged_at TIMESTAMP WITH TIME ZONE, 
    notified_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(ai_task_id) REFERENCES ai_tasks (id), 
    FOREIGN KEY(asset_id) REFERENCES assets (id), 
    FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions (id), 
    CONSTRAINT uq_signal_event UNIQUE (strategy_version_id, asset_id, timeframe, bar_timestamp)
);

CREATE INDEX ix_signals_state ON signals (state);

CREATE TABLE backtest_results (
    id SERIAL NOT NULL, 
    backtest_run_id INTEGER NOT NULL, 
    summary_json JSON NOT NULL, 
    equity_curve_json JSON NOT NULL, 
    metrics_json JSON NOT NULL, 
    result_hash VARCHAR(64) NOT NULL, 
    calculated_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(backtest_run_id) REFERENCES backtest_runs (id) ON DELETE CASCADE, 
    UNIQUE (backtest_run_id)
);

CREATE TABLE backtest_trades (
    id SERIAL NOT NULL, 
    backtest_run_id INTEGER NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    direction VARCHAR(8) NOT NULL, 
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL, 
    entry_price NUMERIC(20, 8) NOT NULL, 
    exit_time TIMESTAMP WITH TIME ZONE, 
    exit_price NUMERIC(20, 8), 
    quantity NUMERIC(24, 10) NOT NULL, 
    fees NUMERIC(20, 8) NOT NULL, 
    slippage NUMERIC(20, 8) NOT NULL, 
    pnl NUMERIC(24, 8), 
    pnl_pct NUMERIC(16, 8), 
    r_multiple NUMERIC(16, 6), 
    mae NUMERIC(20, 8), 
    mfe NUMERIC(20, 8), 
    entry_reason VARCHAR(128), 
    exit_reason VARCHAR(128), 
    ambiguous_fill BOOLEAN NOT NULL, 
    strategy_version VARCHAR(64) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(backtest_run_id) REFERENCES backtest_runs (id) ON DELETE CASCADE
);

CREATE INDEX ix_backtest_trades_run ON backtest_trades (backtest_run_id);

CREATE TABLE paper_orders (
    id SERIAL NOT NULL, 
    account_id INTEGER NOT NULL, 
    signal_id INTEGER, 
    asset_id INTEGER NOT NULL, 
    side VARCHAR(8) NOT NULL, 
    order_type VARCHAR(16) NOT NULL, 
    quantity NUMERIC(24, 10) NOT NULL, 
    limit_price NUMERIC(20, 8), 
    stop_price NUMERIC(20, 8), 
    status VARCHAR(16) NOT NULL, 
    reason VARCHAR(128), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    filled_at TIMESTAMP WITH TIME ZONE, 
    fill_price NUMERIC(20, 8), 
    fees NUMERIC(20, 8) NOT NULL, 
    slippage NUMERIC(20, 8) NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT ck_paper_order_side CHECK (side in ('BUY','SELL')), 
    FOREIGN KEY(account_id) REFERENCES paper_accounts (id) ON DELETE CASCADE, 
    FOREIGN KEY(asset_id) REFERENCES assets (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id)
);

CREATE TABLE signal_outcomes (
    id SERIAL NOT NULL, 
    signal_id INTEGER NOT NULL, 
    outcome_state VARCHAR(32) NOT NULL, 
    entry_price NUMERIC(20, 8), 
    exit_price NUMERIC(20, 8), 
    pnl_pct NUMERIC(16, 8), 
    mae NUMERIC(20, 8), 
    mfe NUMERIC(20, 8), 
    evaluated_at TIMESTAMP WITH TIME ZONE, 
    notes TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id) ON DELETE CASCADE, 
    UNIQUE (signal_id)
);

CREATE TABLE backtest_metrics (
    id SERIAL NOT NULL, 
    backtest_result_id INTEGER NOT NULL, 
    metric_group VARCHAR(32) NOT NULL, 
    metric_name VARCHAR(64) NOT NULL, 
    metric_value NUMERIC(24, 10), 
    metric_text VARCHAR(64), 
    is_available BOOLEAN NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(backtest_result_id) REFERENCES backtest_results (id) ON DELETE CASCADE, 
    CONSTRAINT uq_backtest_metric UNIQUE (backtest_result_id, metric_group, metric_name)
);

CREATE TABLE paper_trades (
    id SERIAL NOT NULL, 
    account_id INTEGER NOT NULL, 
    order_id INTEGER, 
    asset_id INTEGER NOT NULL, 
    direction VARCHAR(8) NOT NULL, 
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL, 
    entry_price NUMERIC(20, 8) NOT NULL, 
    exit_time TIMESTAMP WITH TIME ZONE, 
    exit_price NUMERIC(20, 8), 
    quantity NUMERIC(24, 10) NOT NULL, 
    fees NUMERIC(20, 8) NOT NULL, 
    slippage NUMERIC(20, 8) NOT NULL, 
    pnl NUMERIC(24, 8), 
    r_multiple NUMERIC(16, 6), 
    reason VARCHAR(128), 
    strategy_version VARCHAR(64), 
    PRIMARY KEY (id), 
    FOREIGN KEY(account_id) REFERENCES paper_accounts (id) ON DELETE CASCADE, 
    FOREIGN KEY(asset_id) REFERENCES assets (id), 
    FOREIGN KEY(order_id) REFERENCES paper_orders (id)
);

CREATE INDEX ix_paper_trades_account ON paper_trades (account_id, entry_time);

INSERT INTO alembic_version (version_num) VALUES ('0001_initial') RETURNING alembic_version.version_num;

INFO  [alembic.runtime.migration] Running upgrade 0001_initial -> 0002_immutability, enforce strategy version immutability and reproducibility
-- Running upgrade 0001_initial -> 0002_immutability

CREATE OR REPLACE FUNCTION quantlab_reject_strategy_version_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.dsl_json IS DISTINCT FROM OLD.dsl_json
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.immutable_hash IS DISTINCT FROM OLD.immutable_hash THEN
                RAISE EXCEPTION
                    'strategy_versions rows are immutable: create a new version instead';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;;

CREATE TRIGGER trg_strategy_versions_immutable
        BEFORE UPDATE ON strategy_versions
        FOR EACH ROW
        EXECUTE FUNCTION quantlab_reject_strategy_version_mutation();;

CREATE OR REPLACE FUNCTION quantlab_reject_result_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.result_hash IS NOT NULL
               AND (NEW.summary_json IS DISTINCT FROM OLD.summary_json
                    OR NEW.metrics_json IS DISTINCT FROM OLD.metrics_json
                    OR NEW.equity_curve_json IS DISTINCT FROM OLD.equity_curve_json
                    OR NEW.result_hash IS DISTINCT FROM OLD.result_hash) THEN
                RAISE EXCEPTION 'completed backtest results are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;;

CREATE TRIGGER trg_backtest_results_immutable
        BEFORE UPDATE ON backtest_results
        FOR EACH ROW
        EXECUTE FUNCTION quantlab_reject_result_mutation();;

UPDATE alembic_version SET version_num='0002_immutability' WHERE alembic_version.version_num = '0001_initial';

COMMIT;

