--
-- PostgreSQL database dump
--

\restrict t1HP24ZVWpqEaXMZnGT4Lf6mCUgTCFW82B14NRPCkgrjqWz1ggB8YCtDKbc2N9a

-- Dumped from database version 15.18
-- Dumped by pg_dump version 15.18

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: corporate_actions; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.corporate_actions (
    id integer NOT NULL,
    stock_code character varying(6) NOT NULL,
    stock_name character varying(30),
    ex_date date NOT NULL,
    cash_div numeric(10,4) DEFAULT 0,
    bonus_ratio numeric(10,4) DEFAULT 0,
    transfer_ratio numeric(10,4) DEFAULT 0,
    rights_ratio numeric(10,4) DEFAULT 0,
    rights_price numeric(10,2) DEFAULT 0,
    source character varying(20) DEFAULT 'detect'::character varying,
    confirmed boolean DEFAULT false,
    confirmed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.corporate_actions OWNER TO stock;

--
-- Name: corporate_actions_id_seq; Type: SEQUENCE; Schema: public; Owner: stock
--

CREATE SEQUENCE public.corporate_actions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.corporate_actions_id_seq OWNER TO stock;

--
-- Name: corporate_actions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: stock
--

ALTER SEQUENCE public.corporate_actions_id_seq OWNED BY public.corporate_actions.id;


--
-- Name: download_history; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.download_history (
    id integer NOT NULL,
    task_type character varying(20) NOT NULL,
    data_source character varying(20),
    start_date date,
    end_date date,
    status character varying(10) DEFAULT 'running'::character varying,
    rows_downloaded integer DEFAULT 0,
    elapsed_ms integer,
    error_message text,
    started_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    finished_at timestamp without time zone
);


ALTER TABLE public.download_history OWNER TO stock;

--
-- Name: download_history_id_seq; Type: SEQUENCE; Schema: public; Owner: stock
--

CREATE SEQUENCE public.download_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.download_history_id_seq OWNER TO stock;

--
-- Name: download_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: stock
--

ALTER SEQUENCE public.download_history_id_seq OWNED BY public.download_history.id;


--
-- Name: entity_meta; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.entity_meta (
    stock_code character varying(6) NOT NULL,
    stock_type character varying(10) DEFAULT 'stock'::character varying NOT NULL,
    ipo_date date,
    delist_date date,
    listing_status character varying(16) DEFAULT 'ACTIVE'::character varying,
    total_shares bigint
);


ALTER TABLE public.entity_meta OWNER TO stock;

--
-- Name: failed_downloads; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.failed_downloads (
    id integer NOT NULL,
    exchange character varying(4) NOT NULL,
    trade_date date NOT NULL,
    data_type character varying(20) NOT NULL,
    error_msg text,
    retry_count smallint DEFAULT 0,
    status character varying(10) DEFAULT 'pending'::character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    resolved_at timestamp without time zone
);


ALTER TABLE public.failed_downloads OWNER TO stock;

--
-- Name: failed_downloads_id_seq; Type: SEQUENCE; Schema: public; Owner: stock
--

CREATE SEQUENCE public.failed_downloads_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.failed_downloads_id_seq OWNER TO stock;

--
-- Name: failed_downloads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: stock
--

ALTER SEQUENCE public.failed_downloads_id_seq OWNED BY public.failed_downloads.id;


--
-- Name: indicator_calc_log; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.indicator_calc_log (
    id integer NOT NULL,
    stock_code character varying(10) NOT NULL,
    calc_date date NOT NULL,
    params_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    row_count integer DEFAULT 0,
    status character varying(20) DEFAULT 'success'::character varying NOT NULL,
    error_detail text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.indicator_calc_log OWNER TO stock;

--
-- Name: indicator_calc_log_id_seq; Type: SEQUENCE; Schema: public; Owner: stock
--

CREATE SEQUENCE public.indicator_calc_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.indicator_calc_log_id_seq OWNER TO stock;

--
-- Name: indicator_calc_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: stock
--

ALTER SEQUENCE public.indicator_calc_log_id_seq OWNED BY public.indicator_calc_log.id;


--
-- Name: stock_indicators_atr; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.stock_indicators_atr (
    stock_code character varying(10) NOT NULL,
    trade_date date NOT NULL,
    atr numeric(12,4),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.stock_indicators_atr OWNER TO stock;

--
-- Name: stock_indicators_boll; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.stock_indicators_boll (
    stock_code character varying(10) NOT NULL,
    trade_date date NOT NULL,
    upper numeric(12,4),
    mid numeric(12,4),
    lower numeric(12,4),
    pct_b numeric(8,4),
    width numeric(8,4),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.stock_indicators_boll OWNER TO stock;

--
-- Name: stock_indicators_ma; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.stock_indicators_ma (
    stock_code character varying(10) NOT NULL,
    trade_date date NOT NULL,
    ma5 numeric(12,4),
    ma20 numeric(12,4),
    ma60 numeric(12,4),
    ma250 numeric(12,4),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.stock_indicators_ma OWNER TO stock;

--
-- Name: stock_indicators_macd; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.stock_indicators_macd (
    stock_code character varying(10) NOT NULL,
    trade_date date NOT NULL,
    dif numeric(12,4),
    dea numeric(12,4),
    hist numeric(12,4),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.stock_indicators_macd OWNER TO stock;

--
-- Name: stock_indicators_rsi; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.stock_indicators_rsi (
    stock_code character varying(10) NOT NULL,
    trade_date date NOT NULL,
    rsi numeric(8,4),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.stock_indicators_rsi OWNER TO stock;

--
-- Name: stock_indicators_volume; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.stock_indicators_volume (
    stock_code character varying(10) NOT NULL,
    trade_date date NOT NULL,
    vol_ma5 numeric(18,4),
    vol_ratio numeric(8,4),
    obv numeric(18,4),
    obv_ma5 numeric(18,4),
    obv_ma10 numeric(18,4),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.stock_indicators_volume OWNER TO stock;

--
-- Name: stock_industry; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.stock_industry (
    stock_code character varying(6) NOT NULL,
    exchange character varying(4) NOT NULL,
    industry_sw character varying(20),
    industry_exchange character varying(20),
    updated_at date
);


ALTER TABLE public.stock_industry OWNER TO stock;

--
-- Name: strategy_param_log; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.strategy_param_log (
    id integer NOT NULL,
    strategy_name character varying(30) NOT NULL,
    param_key character varying(30) NOT NULL,
    old_value text,
    new_value text,
    changed_by character varying(20) DEFAULT 'manual'::character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.strategy_param_log OWNER TO stock;

--
-- Name: strategy_param_log_id_seq; Type: SEQUENCE; Schema: public; Owner: stock
--

CREATE SEQUENCE public.strategy_param_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.strategy_param_log_id_seq OWNER TO stock;

--
-- Name: strategy_param_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: stock
--

ALTER SEQUENCE public.strategy_param_log_id_seq OWNED BY public.strategy_param_log.id;


--
-- Name: strategy_scan_tasks; Type: TABLE; Schema: public; Owner: stock
--

CREATE TABLE public.strategy_scan_tasks (
    task_id character varying(16) NOT NULL,
    version character varying(16),
    status character varying(16) DEFAULT 'pending'::character varying,
    total_combos integer DEFAULT 0,
    completed integer DEFAULT 0,
    best_params jsonb,
    best_sharpe numeric(8,4),
    results jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    finished_at timestamp without time zone
);


ALTER TABLE public.strategy_scan_tasks OWNER TO stock;

--
-- Name: corporate_actions id; Type: DEFAULT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.corporate_actions ALTER COLUMN id SET DEFAULT nextval('public.corporate_actions_id_seq'::regclass);


--
-- Name: download_history id; Type: DEFAULT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.download_history ALTER COLUMN id SET DEFAULT nextval('public.download_history_id_seq'::regclass);


--
-- Name: failed_downloads id; Type: DEFAULT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.failed_downloads ALTER COLUMN id SET DEFAULT nextval('public.failed_downloads_id_seq'::regclass);


--
-- Name: indicator_calc_log id; Type: DEFAULT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.indicator_calc_log ALTER COLUMN id SET DEFAULT nextval('public.indicator_calc_log_id_seq'::regclass);


--
-- Name: strategy_param_log id; Type: DEFAULT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.strategy_param_log ALTER COLUMN id SET DEFAULT nextval('public.strategy_param_log_id_seq'::regclass);


--
-- Name: corporate_actions corporate_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT corporate_actions_pkey PRIMARY KEY (id);


--
-- Name: corporate_actions corporate_actions_stock_code_ex_date_key; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT corporate_actions_stock_code_ex_date_key UNIQUE (stock_code, ex_date);


--
-- Name: download_history download_history_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.download_history
    ADD CONSTRAINT download_history_pkey PRIMARY KEY (id);


--
-- Name: entity_meta entity_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.entity_meta
    ADD CONSTRAINT entity_meta_pkey PRIMARY KEY (stock_code, stock_type);


--
-- Name: failed_downloads failed_downloads_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.failed_downloads
    ADD CONSTRAINT failed_downloads_pkey PRIMARY KEY (id);


--
-- Name: indicator_calc_log indicator_calc_log_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.indicator_calc_log
    ADD CONSTRAINT indicator_calc_log_pkey PRIMARY KEY (id);


--
-- Name: indicator_calc_log indicator_calc_log_stock_code_calc_date_key; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.indicator_calc_log
    ADD CONSTRAINT indicator_calc_log_stock_code_calc_date_key UNIQUE (stock_code, calc_date);


--
-- Name: stock_indicators_atr stock_indicators_atr_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.stock_indicators_atr
    ADD CONSTRAINT stock_indicators_atr_pkey PRIMARY KEY (stock_code, trade_date);


--
-- Name: stock_indicators_boll stock_indicators_boll_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.stock_indicators_boll
    ADD CONSTRAINT stock_indicators_boll_pkey PRIMARY KEY (stock_code, trade_date);


--
-- Name: stock_indicators_ma stock_indicators_ma_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.stock_indicators_ma
    ADD CONSTRAINT stock_indicators_ma_pkey PRIMARY KEY (stock_code, trade_date);


--
-- Name: stock_indicators_macd stock_indicators_macd_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.stock_indicators_macd
    ADD CONSTRAINT stock_indicators_macd_pkey PRIMARY KEY (stock_code, trade_date);


--
-- Name: stock_indicators_rsi stock_indicators_rsi_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.stock_indicators_rsi
    ADD CONSTRAINT stock_indicators_rsi_pkey PRIMARY KEY (stock_code, trade_date);


--
-- Name: stock_indicators_volume stock_indicators_volume_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.stock_indicators_volume
    ADD CONSTRAINT stock_indicators_volume_pkey PRIMARY KEY (stock_code, trade_date);


--
-- Name: stock_industry stock_industry_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.stock_industry
    ADD CONSTRAINT stock_industry_pkey PRIMARY KEY (stock_code, exchange);


--
-- Name: strategy_param_log strategy_param_log_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.strategy_param_log
    ADD CONSTRAINT strategy_param_log_pkey PRIMARY KEY (id);


--
-- Name: strategy_scan_tasks strategy_scan_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: stock
--

ALTER TABLE ONLY public.strategy_scan_tasks
    ADD CONSTRAINT strategy_scan_tasks_pkey PRIMARY KEY (task_id);


--
-- Name: idx_atr_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_atr_date ON public.stock_indicators_atr USING btree (trade_date);


--
-- Name: idx_boll_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_boll_date ON public.stock_indicators_boll USING btree (trade_date);


--
-- Name: idx_ca_confirmed; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_ca_confirmed ON public.corporate_actions USING btree (confirmed, ex_date);


--
-- Name: idx_ca_ex_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_ca_ex_date ON public.corporate_actions USING btree (ex_date);


--
-- Name: idx_dh_type_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_dh_type_date ON public.download_history USING btree (task_type, started_at DESC);


--
-- Name: idx_em_status; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_em_status ON public.entity_meta USING btree (listing_status);


--
-- Name: idx_fd_status; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_fd_status ON public.failed_downloads USING btree (status, exchange);


--
-- Name: idx_icl_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_icl_date ON public.indicator_calc_log USING btree (calc_date);


--
-- Name: idx_ma_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_ma_date ON public.stock_indicators_ma USING btree (trade_date);


--
-- Name: idx_macd_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_macd_date ON public.stock_indicators_macd USING btree (trade_date);


--
-- Name: idx_rsi_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_rsi_date ON public.stock_indicators_rsi USING btree (trade_date);


--
-- Name: idx_volume_date; Type: INDEX; Schema: public; Owner: stock
--

CREATE INDEX idx_volume_date ON public.stock_indicators_volume USING btree (trade_date);


--
-- PostgreSQL database dump complete
--

\unrestrict t1HP24ZVWpqEaXMZnGT4Lf6mCUgTCFW82B14NRPCkgrjqWz1ggB8YCtDKbc2N9a

