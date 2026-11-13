<template>
<div>
  <n-space align="center" :wrap="false" style="margin-bottom:8px;overflow-x:auto;scrollbar-width:none">
    <n-button size="tiny" @click="$emit('back')"><AppIcon name="arrow-left" :size="13" /> 返回</n-button>
    <StockSuggestInput v-model:value="inputCode" size="tiny" width="170px" @select="onPickStock" @enter="onQuery" />
    <n-button class="cta" type="primary" size="tiny" @click="onQuery">查询</n-button>
    <n-button size="tiny" title="带当前股票跳转问答页" @click="askAi"><AppIcon name="compass" :size="13" /> 问 AI</n-button>
    <n-select v-model:value="adj" @update:value="reloadChart" size="tiny" style="width:105px" :options="adjOptions" />
    <n-button-group size="tiny">
      <n-button v-for="p in PERIODS" :key="p.v" size="tiny" :type="period===p.v?'primary':'default'" @click="switchPeriod(p.v)">{{ p.t }}</n-button>
    </n-button-group>
    <n-button-group size="tiny">
      <n-button v-for="r in RANGES" :key="r.d" size="tiny" :type="range===r.d?'primary':'default'" @click="switchRange(r.d)">{{ r.t }}</n-button>
    </n-button-group>
    <n-button-group size="tiny">
      <n-button size="tiny" :disabled="!prevCode" @click="jump(prevCode)"><AppIcon name="arrow-left" :size="13" /> 上一只</n-button>
      <n-button size="tiny" :disabled="!nextCode" @click="jump(nextCode)">下一只 <AppIcon name="arrow-right" :size="13" /></n-button>
    </n-button-group>
  </n-space>

  <n-spin v-if="loading" />
  <n-empty v-else-if="notFound" description="未找到该证券，请检查代码" style="padding:40px" />
  <div v-else-if="loadError" style="padding:40px;text-align:center">
    <n-empty :description="loadError" />
    <n-button size="small" style="margin-top:12px" @click="load">重试</n-button>
  </div>
  <template v-else-if="detail">
    <!-- 左边缘盘中执行卡：hover 小图标 → 左侧抽屉；操作动作由本页滚动/开图执行 -->
    <IntradayPlaybook :code="detail.stock_code || code" :name="detail.stock_name" @action="onPlaybookAction" />
    <!-- 分组状态：持仓为 portfolio 固有状态（不可移除）；自选/动态组 hover 出 ✕ 点击确认移除；末尾 + 加组 -->
    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:8px">
      <span style="font-size: var(--text-meta);color:var(--c-text-dim)">分组</span>
      <n-tag v-if="stockGroups.in_portfolio" size="small" type="info" :bordered="false">持仓</n-tag>
      <n-tag v-for="g in stockGroups.groups" :key="g.id" size="small" closable :bordered="false"
        class="dt-grp-tag" :type="g.is_default ? 'warning' : 'default'"
        :title="g.added_at ? '加入于 ' + g.added_at : ''"
        @close="confirmRemoveGroup(g)">{{ g.name }}</n-tag>
      <n-button size="tiny" dashed @click="openAddGroup"><AppIcon name="plus" :size="12" />  分组</n-button>
    </div>

    <!-- Summary Cards - unified stat-row style（第 5 格 = 最新策略信号，完整理由在 tooltip） -->
    <StatStrip :items="summaryItems" />

    <!-- Left-Right Layout -->
    <div style="display:flex;gap:12px;flex-wrap:wrap">
      <div style="flex:1;min-width:320px">
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text);display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <AppIcon name="clock" :size="13" />  分钟K线
            <span style="font-size: var(--text-meta);color:var(--c-text-dim)">腾讯实时 / 不复权 / 分钟线 30s</span>
            <n-button-group size="tiny">
              <n-button v-for="p in MINK_PERIODS" :key="p.v" size="tiny" :type="minkPeriod===p.v?'primary':'default'" @click="minkPeriod=p.v">{{ p.t }}</n-button>
            </n-button-group>
            <span style="margin-left:auto;display:flex;align-items:center;gap:8px">
              <span class="mink-refresh" :class="{on: quote?.is_realtime}"
                :title="quote?.is_realtime ? '实时报价：盘中每 5 秒自动刷新' : '实时报价：非交易时段每 30 秒自动刷新'">
                {{ quote ? (quote.is_realtime ? '盘中 5s' : '非交易 30s') : '—' }}
              </span>
              <span class="mink-status" :class="minkStatusClass" :title="minkLastBarTitle">{{ minkStatus }}</span>
              <n-button size="tiny" :type="minkPaused?'primary':'default'" @click="toggleMinkPause">{{ minkPaused ? '继续' : '暂停' }}</n-button>
              <n-button size="tiny" @click="openFs" title="多栏分时全屏对比（Alt+F）">全屏</n-button>
            </span>
          </h4>
          <!-- 分时辅助层：价位/VWAP带/开盘区间/事件/量分布 -->
          <div class="mink-toolbar">
            <n-button-group size="tiny">
              <n-button size="tiny" :type="minkType==='candle'?'primary':'default'"
                title="蜡烛图" @click="minkType='candle'; drawMink()">K线</n-button>
              <n-button size="tiny" :type="minkType==='line'?'primary':'default'"
                title="分时线" @click="minkType='line'; drawMink()">分时</n-button>
            </n-button-group>
            <n-button-group size="tiny">
              <n-button size="tiny" :type="minkMaMode==='ma'?'primary':'default'"
                title="MA5 / MA10" @click="minkMaMode='ma'; drawMink()">MA</n-button>
              <n-button size="tiny" :type="minkMaMode==='ema'?'primary':'default'"
                title="EMA9 / EMA21" @click="minkMaMode='ema'; drawMink()">EMA</n-button>
            </n-button-group>
            <n-button size="tiny" @click="showLevels=!showLevels"
              title="关键价位：昨高/昨低/今开/成本/止损/止盈/涨跌停（远离现价的不画，避免压缩日内波动）">价位</n-button>
            <n-button size="tiny" :type="showBands?'primary':'default'" @click="showBands=!showBands"
              title="VWAP ±1σ / ±2σ 标准差带">VWAP带</n-button>
            <n-button size="tiny" :type="showOr?'primary':'default'" @click="showOr=!showOr"
              title="开盘 15/30 分钟区间（m30/m60 不适用）">开盘区间</n-button>
            <n-button size="tiny" :type="showEvents?'primary':'default'" @click="showEvents=!showEvents"
              title="盘中提醒 / 重要资讯标记">事件</n-button>
            <n-button size="tiny" :disabled="!minkDayBars.length" @click="openVp"
              title="当日成交量分布 / POC / VAH / VAL">量分布</n-button>
            <n-button size="tiny" :disabled="!minkDayBars.length" @click="openReplay()"
              title="当日分时回放">回放</n-button>
            <n-button size="tiny" :disabled="!minkDayBars.length" @click="openCompare"
              title="多股对比（当日起点=0%）">对比</n-button>
            <n-button size="tiny" @click="exportMink" title="导出分时图 PNG">导出</n-button>
            <span style="font-size:var(--text-meta);color:var(--c-text-faint);margin-left:2px">hover 看 VWAP 偏离 / RVOL · Alt+1..5 切换周期</span>
            <span class="mink-p3" title="分钟级模型需 P3 数据达标（20 个完整交易日 + 1 万根 bar）后训练；进度见状态页「分钟数据」">P3 模型 · 待数据</span>
          </div>
          <!-- 实时报价条：盘中 5s / 非交易时段 30s；失败时回退到最近日线收盘并给出重试 -->
          <div ref="quoteBarEl" class="quote-bar" :class="{ 'quote-off': !quote }">
            <div class="quote-quote">
              <div class="quote-kicker">
                <span>现价</span>
                <span class="quote-help" title="现价=实时快照最新价；涨跌=相对昨收；盘中每 5 秒刷新，非交易时段 30 秒；买一/卖一为五档第一档。AI 解读=结合实时行情/分钟结构/资讯/模型信号生成，非预测、非投资建议。">?</span>
              </div>
              <div class="quote-price-line">
                <span class="quote-price" :style="{color: quoteColor}">{{ quotePriceText }}</span>
                <span class="quote-chg" :style="{color: quoteColor}">{{ quoteChgText }}</span>
              </div>
              <div class="quote-subline">
                <span class="quote-status" :class="quoteTone"
                  :title="quote?.is_realtime ? '盘中实时（红点脉冲）' : '午休/收盘/未开盘或快照不可用'">
                  <i></i>{{ quote?.session_status || (quoteError ? '快照不可用' : '加载中') }}
                </span>
                <span v-if="quote?.is_realtime && quote.timestamp">{{ quote.timestamp.slice(11) }} 更新</span>
                <span v-else-if="quote?.trade_date">{{ quote.trade_date }} 收盘</span>
                <span v-else-if="detail">最近收盘 {{ detail.latest_trade_date }}</span>
              </div>
            </div>
            <div v-if="quote" class="quote-stats">
              <div class="quote-stat"><span class="quote-stat-label">今开</span><b class="quote-stat-value">{{ qfmt(quote.open) }}</b></div>
              <div class="quote-stat"><span class="quote-stat-label">最高</span><b class="quote-stat-value">{{ qfmt(quote.high) }}</b></div>
              <div class="quote-stat"><span class="quote-stat-label">最低</span><b class="quote-stat-value">{{ qfmt(quote.low) }}</b></div>
              <div class="quote-stat"><span class="quote-stat-label">均价</span><b class="quote-stat-value">{{ qfmt(quote.avg_price) }}</b></div>
              <div class="quote-stat"><span class="quote-stat-label">量比</span><b class="quote-stat-value">{{ qfmt(quote.volume_ratio) }}</b></div>
              <div class="quote-stat"><span class="quote-stat-label">成交额</span><b class="quote-stat-value">{{ fmtMoney(quote.amount) }}</b></div>
              <div class="quote-stat"><span class="quote-stat-label">换手</span><b class="quote-stat-value">{{ quote.turnover_pct != null ? quote.turnover_pct.toFixed(2) + '%' : '—' }}</b></div>
              <div class="quote-stat" title="最后一根完整 bar 的成交量 / 当日已完成 bar 的均量；历史同时刻基线待 P3 数据积累">
                <span class="quote-stat-label">RVOL</span>
                <b class="quote-stat-value">{{ minkRvol != null ? minkRvol.toFixed(2) + 'x' : '—' }}</b>
              </div>
            </div>
            <div class="quote-actions">
              <n-button size="tiny" text :loading="quoteLoading" title="立即刷新实时快照" @click="loadQuote">刷新</n-button>
              <n-button size="tiny" secondary :loading="aiLoading"
                title="AI 解读：结合实时行情 / 分钟结构 / 资讯 / 模型信号，非预测" @click="loadAiInsight">
                <AppIcon name="compass" :size="12" /> AI 解读
              </n-button>
            </div>
            <div v-if="quote && (quote.bids?.length || quote.asks?.length)" class="quote-depth">
              <span class="quote-depth-label">五档</span>
              <span class="bid">买一 <b>{{ qfmt(quote.bids?.[0]?.price) }}</b> × {{ quote.bids?.[0]?.volume ?? '—' }}</span>
              <span class="ask">卖一 <b>{{ qfmt(quote.asks?.[0]?.price) }}</b> × {{ quote.asks?.[0]?.volume ?? '—' }}</span>
              <span v-if="depthStats" class="quote-depth-stat"
                title="五档快照：买盘总量 vs 卖盘总量，不代表成交方向">
                盘口 <b :style="{color: depthStats.imb >= 0 ? UP_COLOR : DOWN_COLOR}">{{ depthStats.imb != null ? (depthStats.imb * 100).toFixed(0) + '%' : '—' }}</b>
                <span class="qd-muted">{{ depthStats.imb >= 0 ? '买盘占优' : '卖盘占优' }}</span>
                <span v-if="depthStats.spread != null"> · 价差 {{ depthStats.spread.toFixed(1) }}bp</span>
              </span>
              <span v-if="quote.cost_price" class="quote-depth-stat">
                成本 <b>{{ qfmt(quote.cost_price) }}</b>
                <span v-if="quote.stop_price"> · 止损 <b style="color:var(--c-up)">{{ qfmt(quote.stop_price) }}</b></span>
                <span v-if="quote.take_profit_price"> · 止盈 <b style="color:var(--c-down)">{{ qfmt(quote.take_profit_price) }}</b></span>
              </span>
            </div>
            <div v-else-if="!quote" class="quote-hint">
              实时报价接口不可用，当前显示最近日线收盘；若刚更新代码，请重启后端后点「刷新」。
            </div>
          </div>
          <!-- AI 盘面解读：只解读数据，不预测涨跌；结果 90s 服务端缓存 -->
          <div v-if="aiLoading || aiInsight || aiError" ref="aiCardEl" class="ai-card">
            <div class="ai-head">
              <span class="ai-badge">AI 盘面解读</span>
              <span class="ai-note">非预测 · 非投资建议</span>
              <span v-if="aiEvent" class="ai-event-badge">事件触发</span>
              <span v-if="aiInsight?.cached" class="ai-cached">缓存</span>
              <n-button size="tiny" text style="margin-left:auto" @click="closeAi">收起</n-button>
            </div>
            <div v-if="aiInsight?.event" class="ai-event-line" :title="aiInsight.event.body || ''">
              事件：{{ aiInsight.event.title }}<span v-if="aiInsight.event.body"> · {{ aiInsight.event.body }}</span>
            </div>
            <div v-if="aiLoading" class="ai-loading">正在结合实时行情 / 分钟结构 / 资讯 / 模型信号生成解读…</div>
            <div v-else-if="aiError" class="ai-error">{{ aiError }}</div>
            <template v-else-if="aiInsight">
              <div class="ai-stance-row">
                <span class="ai-stance" :class="aiStanceClass">{{ aiInsight.stance }}</span>
                <span class="ai-summary">{{ aiInsight.summary }}</span>
              </div>
              <ul v-if="aiInsight.observations?.length" class="ai-points">
                <li v-for="(p, i) in aiInsight.observations" :key="'o' + i">{{ p }}</li>
              </ul>
              <template v-if="aiInsight.risks?.length">
                <div class="ai-sub">风险观察</div>
                <ul class="ai-points risk">
                  <li v-for="(p, i) in aiInsight.risks" :key="'r' + i">{{ p }}</li>
                </ul>
              </template>
              <template v-if="aiInsight.watch?.length">
                <div class="ai-sub">观察价位</div>
                <ul class="ai-points">
                  <li v-for="(p, i) in aiInsight.watch" :key="'w' + i">{{ p }}</li>
                </ul>
              </template>
              <div class="ai-foot">
                数据时间 {{ aiInsight.data_time || '—' }} · 生成于 {{ aiInsight.generated_at }}
                <span v-if="aiInsight.model"> · {{ aiInsight.model }}</span>
              </div>
            </template>
          </div>
          <div :id="'c0'" style="width:100%;height:260px"></div>
          <div v-if="minkLevelChips.length" class="mink-levels">
            <span v-for="l in minkLevelChips" :key="l.name + l.value">
              <i :style="{background:l.color}"></i>{{ l.name }} <b>{{ l.value.toFixed(2) }}</b>
            </span>
          </div>
        </div>
        <!-- 盘中相对强弱：与分时同周期、同窗口，起点=1 -->
        <div v-if="benchMink.length && minkDayBars.length > 1" style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="trending-up" :size="13" />  盘中相对强弱 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">个股 vs 沪深300（当日起点=0%，超额=百分点）</span></h4>
          <div :id="'c13'" style="width:100%;height:110px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="candlestick" :size="13" />  K线图 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">{{dateRange}}</span></h4>
          <div :id="'c1'" style="width:100%;height:340px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="trending-up" :size="13" />  均线 (MA5/10/20/30/60/120/180)</h4>
          <div :id="'c6'" style="width:100%;height:180px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="bar-chart-2" :size="13" />  成交量</h4>
          <div :id="'c2'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="zap" :size="13" />  MACD</h4>
          <div :id="'c3'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="activity" :size="13" />  RSI</h4>
          <div :id="'c4'" style="width:100%;height:160px"></div>
        </div>
        <div v-if="benchData.length" style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="sliders" :size="13" />  相对强弱 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">个股 vs 沪深300（窗口起点=1）</span></h4>
          <div :id="'c8'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="refresh" :size="13" />  换手率 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">%（含分位线）</span></h4>
          <div :id="'c9'" style="width:100%;height:140px"></div>
        </div>
        <div v-if="marginData.length > 2" style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="arrow-left-right" :size="13" />  两融余额 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">融资 / 合计（亿元）</span></h4>
          <div :id="'c10'" style="width:100%;height:140px"></div>
        </div>
        <!-- 带右侧 Y 轴的图统一殿后（PE分位%/资金流累计/ATR%），避免中段双轴观感错乱 -->
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="percent" :size="13" />  PE历史分位 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">{{peRange}}</span></h4>
          <div :id="'c5'" style="width:100%;height:160px"></div>
        </div>
        <div v-if="mfData.length" style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="dollar-sign" :size="13" />  资金流 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">主力净流入(超大+大单, 万元) / 累计</span></h4>
          <div :id="'c7'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="maximize" :size="13" />  ATR <span style="font-size: var(--text-meta);color:var(--c-text-dim)">14日真实波幅 / 占价比</span></h4>
          <div :id="'c11'" style="width:100%;height:140px"></div>
        </div>
      </div>

      <!-- Right sidebar: 盘中提醒 → 筹码 → 信号回看 → 基本面 → 行情概览 → 讨论/资讯 tab -->
      <div style="width:100%;max-width:320px;display:flex;flex-direction:column;gap:12px" class="detail-sidebar">
        <!-- 盘中提醒：后端 watcher 写 risk_alerts，30s 轮询；Chrome 通知走 WS 实时推送 -->
        <div ref="alertsPanelEl" class="side-panel">
          <h4 class="side-title"><AppIcon name="bell" :size="13" />  盘中提醒 <span style="font-size: var(--text-meta);color:var(--c-text-dim)">近 8 条 / 实时推送</span></h4>
          <div v-if="intradayAlerts.length" class="ia-list">
            <div v-for="a in intradayAlerts" :key="a.id" class="ia-item">
              <div class="ia-head">
                <span :class="['ia-dot', a.level === 'critical' ? 'crit' : 'warn']"></span>
                <span class="ia-title">{{ a.title }}</span>
                <span class="ia-time">{{ (a.created_at || '').slice(11, 16) }}</span>
              </div>
              <div class="ia-body">{{ a.body }}</div>
            </div>
          </div>
          <n-empty v-else description="暂无盘中提醒" style="padding:6px" />
        </div>

        <!-- 筹码分布 -->
        <div v-if="chipData && chipData.buckets && chipData.buckets.length" class="side-panel">
          <h4 class="side-title" style="flex-wrap:wrap">
            <AppIcon name="pie-chart" :size="13" />  筹码分布
            <span style="font-size: var(--text-meta);color:var(--c-text-dim);flex-basis:100%;margin-left:19px;white-space:normal">
              {{chipData.decay?'换手衰减':'全量'}},现价口径<template v-if="chipData.date_from"> / {{chipData.date_from}}~{{chipData.date_to}}</template>
            </span>
          </h4>
          <div :id="'c12'" style="width:100%;height:280px"></div>
          <div v-if="chipData.profit_ratio!=null" style="font-size: var(--text-meta);color:var(--c-text-dim);margin-top:2px">
            现价 ¥{{chipData.current}} / 获利盘 <span :style="{color:chipData.profit_ratio>=0.5?'var(--c-up)':'var(--c-down)',fontWeight:600}">{{(chipData.profit_ratio*100).toFixed(1)}}%</span>（红=获利 绿=套牢）
          </div>
        </div>

        <!-- 信号回看 -->
        <div v-if="sigStats" class="side-panel">
          <h4 class="side-title"><AppIcon name="list" :size="13" />  信号回看</h4>
          <div style="font-size: var(--text-meta);line-height:1.7;color:var(--c-text-dim)">
            近{{sigStats.years}}年 <b style="color:var(--c-text)">{{sigStats.n}}</b> 条信号（buy {{sigStats.buys}}/sell {{sigStats.sells}}）<br/>
            平均前瞻：5d <span :style="{color:sigStats.f5>=0?'var(--c-up)':'var(--c-down)'}">{{(sigStats.f5*100).toFixed(1)}}%</span>
            10d <span :style="{color:sigStats.f10>=0?'var(--c-up)':'var(--c-down)'}">{{(sigStats.f10*100).toFixed(1)}}%</span>
            20d <span :style="{color:sigStats.f20>=0?'var(--c-up)':'var(--c-down)'}">{{(sigStats.f20*100).toFixed(1)}}%</span>
          </div>
        </div>

        <!-- 基本面 -->
        <div class="side-panel">
          <h4 class="side-title"><AppIcon name="w" :size="13" />  基本面</h4>
          <!-- v-if 判断对象存在即可：industry 可能为空，不能因此隐藏整块 -->
          <table v-if="detail.fundamentals!=null&&Object.keys(detail.fundamentals).length" class="side-table">
            <tr v-for="row in fundRows" :key="row.lbl" style="background:var(--c-card-bg)">
              <td style="width:85px;white-space:nowrap;padding:4px 6px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta);text-align:left">{{row.lbl}}</td>
              <td style="padding:4px 6px;border:1px solid var(--c-border);color:var(--c-text);text-align:left">{{row.val}}</td>
            </tr>
          </table>
          <n-empty v-else-if="!loading" description="暂无基本面数据" style="padding:10px" />
        </div>

        <!-- 行情概览 -->
        <div class="side-panel">
          <h4 class="side-title"><AppIcon name="bar-chart-2" :size="13" />  行情概览</h4>
          <table class="side-table">
            <tr><td>日期</td><td style="padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text)" :style="{background:hoverInfo?'var(--c-up-faint)':'transparent'}">{{hoverInfo?hoverInfo.date:detail.latest_trade_date}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta)">开盘</td><td style="padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text)">¥{{hoverInfo?hoverInfo.open.toFixed(2):(detail.open||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta)">最高</td><td style="padding:4px 8px;border:1px solid var(--c-border)" :style="{color:hoverInfo?'var(--c-up)':'var(--c-text)'}">¥{{hoverInfo?hoverInfo.high.toFixed(2):(detail.high||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta)">最低</td><td style="padding:4px 8px;border:1px solid var(--c-border)" :style="{color:hoverInfo?'var(--c-down)':'var(--c-text)'}">¥{{hoverInfo?hoverInfo.low.toFixed(2):(detail.low||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta)">收盘</td><td style="padding:4px 8px;border:1px solid var(--c-border)"><span style="font-weight:600" :style="{color:hoverInfo?(hoverInfo.close>=hoverInfo.prevClose?'var(--c-up)':'var(--c-down)'):priceColor}">¥{{hoverInfo?hoverInfo.close.toFixed(2):(detail.close||0).toFixed(2)}}</span><span v-if="hoverInfo&&hoverInfo.prevClose" style="font-size: var(--text-meta);margin-left:4px" :style="{color:hoverInfo.close>=hoverInfo.prevClose?'var(--c-up)':'var(--c-down)'}">{{((hoverInfo.close-hoverInfo.prevClose)/hoverInfo.prevClose*100).toFixed(2)}}%</span><span v-else-if="priceChg!=null" style="font-size: var(--text-meta);margin-left:4px" :style="{color:priceColor}">{{priceChg>=0?'+':''}}{{priceChg.toFixed(2)}}%</span></td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta)">成交量</td><td style="padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{hoverInfo?fmt(hoverInfo.volume)+'股':fmt(detail.volume)+'股'}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta)">成交额</td><td style="padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{hoverInfo&&hoverInfo.amount?fmt(hoverInfo.amount)+'元':(detail.amount?fmt(detail.amount)+'元':'-')}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size: var(--text-meta)">换手率</td><td style="padding:4px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{(hoverInfo&&hoverInfo.turnover!=null?hoverInfo.turnover:(detail.turnover!=null?detail.turnover:null))!=null ? (hoverInfo&&hoverInfo.turnover!=null?hoverInfo.turnover:detail.turnover).toFixed(2)+'%':'—'}}</td></tr>
          </table>
        </div>

        <!-- 雪球讨论 / 相关资讯：同一张卡两个 tab；只有一个有数据时自动切到有数据页 -->
        <div v-if="xqHas || newsHas" class="side-panel">
          <n-tabs :value="sentiTab" type="line" size="small" animated
            @update:value="onSentiTabChange">
            <n-tab-pane name="xq" :tab="'雪球讨论' + (xqData ? ' ' + (xqData.count_7d || 0) : '')">
              <template v-if="xqHas">
                <div style="font-size: var(--text-meta);line-height:1.7;color:var(--c-text-dim);margin-bottom:6px">
                  今日 <b style="color:var(--c-text)">{{xqData.today_count}}</b> 帖
                  标记帖 <b style="color:var(--c-text)">{{xqData.mark_ratio!=null?(xqData.mark_ratio*100).toFixed(1)+'%':'-'}}</b>
                  7日互动 <b style="color:var(--c-text)">{{xqData.interactions_7d}}</b>
                </div>
                <!-- LLM 情绪构成（design/06 §9.10）：只统计已打分帖（夜间任务，按热度取） -->
                <div v-if="xqData.sentiment && xqData.sentiment.scored" class="side-well">
                  <div style="display:flex;align-items:center;justify-content:space-between;font-size: var(--text-meta);margin-bottom:4px">
                    <span style="color:var(--c-text-dim)">
                      LLM 情绪
                      <span style="color:var(--c-text-faint)">· {{xqData.sentiment.scored}} 帖已打分</span>
                    </span>
                    <span :style="{color: xqData.sentiment.net>=0 ? UP_COLOR : DOWN_COLOR, fontWeight:600}">
                      净看多 {{ xqData.sentiment.net>=0?'+':'' }}{{ (xqData.sentiment.net*100).toFixed(0) }}%
                    </span>
                  </div>
                  <!-- 占比条：红=看多 灰=中性 绿=看空（方向域，与全站一致） -->
                  <div style="display:flex;height:6px;border-radius:var(--radius-sm);overflow:hidden;background:var(--c-border)">
                    <div v-for="seg in sentiSegs" :key="seg.k" :title="seg.t+' '+seg.n+' 帖'"
                         :style="{width:seg.w+'%', background:seg.c}"></div>
                  </div>
                  <div style="display:flex;gap:10px;font-size: var(--text-meta);margin-top:4px">
                    <span v-for="seg in sentiSegs" :key="seg.k" style="color:var(--c-text-dim)">
                      <span :style="{color:seg.c,fontWeight:600}">{{seg.t}}</span> {{seg.n}}
                    </span>
                  </div>
                </div>
                <div style="display:flex;flex-direction:column;gap:6px">
                  <div v-for="(p,i) in xqData.hot.slice(0, 6)" :key="i" class="side-item">
                    <div style="display:flex;align-items:center;gap:6px;font-size: var(--text-meta);color:var(--c-text-faint);margin-bottom:3px">
                      <span>{{p.created_at.slice(5)}}</span>
                      <span v-if="p.source">{{p.source}}</span>
                      <span v-if="sentiOf(p.sentiment)" :title="p.reason||''"
                            :style="{color:sentiOf(p.sentiment).c, background:'var(--c-neutral-weak)', borderRadius:'8px', padding:'0 5px', fontWeight:600}">{{sentiOf(p.sentiment).t}}</span>
                      <span v-if="p.mark" style="background:var(--c-neutral-weak);border-radius:var(--radius);padding:0 4px">标</span>
                      <span style="margin-left:auto;flex-shrink:0">赞 {{p.like_count}} / 评 {{p.reply_count}}</span>
                    </div>
                    <div class="side-text" :title="p.text">{{p.text}}</div>
                  </div>
                </div>
                <div v-if="xqData.hot.length > 6" class="side-more">仅显示前 6 条热帖</div>
              </template>
              <n-empty v-else description="暂无雪球讨论" style="padding:8px" />
            </n-tab-pane>
            <n-tab-pane name="news" :tab="'相关资讯' + (newsData ? ' ' + (newsData.count || 0) : '')">
              <template v-if="newsHas">
                <div style="font-size: var(--text-meta);line-height:1.7;color:var(--c-text-dim);margin-bottom:6px">
                  <span v-for="(v,k) in newsData.by_category" :key="k" style="margin-right:8px">{{NEWS_CAT_NAME[k]||k}} <b style="color:var(--c-text)">{{v}}</b></span>
                </div>
                <div style="display:flex;flex-direction:column;gap:6px">
                  <div v-for="n in newsData.data.slice(0, 8)" :key="n.id" class="side-item">
                    <div style="display:flex;align-items:center;gap:6px;font-size: var(--text-meta);color:var(--c-text-faint);margin-bottom:3px">
                      <span>{{(n.published_at||'').slice(5)}}</span>
                      <span :style="{color:NEWS_CAT_COLOR[n.category]||'var(--c-text-dim)',fontWeight:600}">{{NEWS_CAT_NAME[n.category]||n.category}}</span>
                      <span v-if="n.importance===3" title="重要度 3：个股重大事件" style="background:var(--c-up-weak);color:var(--c-up);border-radius:var(--radius);padding:0 4px;font-weight:600">重</span>
                      <span v-else-if="n.importance===2" style="background:var(--c-neutral-weak);border-radius:var(--radius);padding:0 4px">中</span>
                      <!-- 未总结才显示「待总结」：imp=1 是合法等级（不显示徽标），不能落到 else 里被当成未总结 -->
                      <span v-else-if="n.importance===null" style="background:var(--c-neutral-weaker);border-radius:var(--radius);padding:0 4px">待总结</span>
                      <span v-if="newsSentiOf(n.sentiment)" :style="{color:newsSentiOf(n.sentiment).c,background:'var(--c-neutral-weak)',borderRadius:'8px',padding:'0 5px',fontWeight:600}">{{newsSentiOf(n.sentiment).t}}</span>
                      <a v-if="n.url" :href="n.url" target="_blank" rel="noopener" style="margin-left:auto;flex-shrink:0;color:var(--c-info)">原文</a>
                    </div>
                    <div class="side-text" :title="n.title">{{n.title}}</div>
                    <div v-if="n.summary" class="side-text" style="color:var(--c-text-dim);margin-top:2px" :title="n.summary">{{n.summary}}</div>
                  </div>
                </div>
                <div v-if="newsData.data.length > 8" class="side-more">仅显示前 8 条，完整列表见资讯页</div>
              </template>
              <n-empty v-else description="暂无相关资讯" style="padding:8px" />
            </n-tab-pane>
          </n-tabs>
        </div>
      </div>
    </div>
  </template>

  <!-- 多栏分时全屏对比：1–3 栏；每栏完整分时功能；左右悬停 + 添加；Esc 退出 -->
  <div v-if="showFs" class="fs-overlay">
    <div class="fs-head">
      <span class="fs-title">分时对比</span>
      <span class="fs-count">{{ fsPanels.length }}/3</span>
      <n-button-group size="tiny">
        <n-button v-for="p in MINK_PERIODS" :key="'fs-'+p.v" size="tiny"
          :type="minkPeriod===p.v?'primary':'default'" @click="minkPeriod=p.v">{{ p.t }}</n-button>
      </n-button-group>
      <n-button-group size="tiny">
        <n-button size="tiny" :type="minkType==='candle'?'primary':'default'"
          @click="minkType='candle'">K线</n-button>
        <n-button size="tiny" :type="minkType==='line'?'primary':'default'"
          @click="minkType='line'">分时</n-button>
      </n-button-group>
      <n-button-group size="tiny">
        <n-button size="tiny" :type="minkMaMode==='ma'?'primary':'default'"
          @click="minkMaMode='ma'">MA</n-button>
        <n-button size="tiny" :type="minkMaMode==='ema'?'primary':'default'"
          @click="minkMaMode='ema'">EMA</n-button>
      </n-button-group>
      <n-button size="tiny" :type="showLevels?'primary':'default'" @click="showLevels=!showLevels">价位</n-button>
      <n-button size="tiny" :type="showBands?'primary':'default'" @click="showBands=!showBands">VWAP带</n-button>
      <n-button size="tiny" :type="showOr?'primary':'default'" @click="showOr=!showOr">开盘区间</n-button>
      <n-button size="tiny" :type="showEvents?'primary':'default'" @click="showEvents=!showEvents">事件</n-button>
      <span class="fs-hint">左右悬停 <b>+</b> 添加 · 最多 3 栏 · Esc 退出</span>
      <span style="margin-left:auto;display:flex;gap:8px">
        <n-button v-if="fsPanels.length < 3" size="small" @click="openFsSearch('right')">+ 添加</n-button>
        <n-button size="small" @click="closeFs">退出全屏</n-button>
      </span>
    </div>
    <div class="fs-panels" :style="{gridTemplateColumns: `repeat(${fsPanels.length}, minmax(0, 1fr))`}">
      <div v-for="(p, i) in fsPanels" :key="p.code" class="fs-panel">
        <div class="fs-panel-head">
          <span class="fs-panel-name">{{ p.name }}</span>
          <span class="fs-panel-code">{{ p.code }}</span>
          <span v-if="p.quote" class="fs-panel-price"
            :style="{color: (p.quote.change_pct >= 0 ? UP_COLOR : DOWN_COLOR)}">
            {{ p.quote.price != null ? p.quote.price.toFixed(2) : '—' }}
            <span class="fs-panel-chg" v-if="p.quote.change_pct != null">
              {{ p.quote.change_pct >= 0 ? '+' : '' }}{{ p.quote.change_pct.toFixed(2) }}%
            </span>
          </span>
          <span v-if="p.loading" class="fs-panel-loading">加载中…</span>
          <span class="fs-panel-tools">
            <n-button size="tiny" text @click="callFsAi(p)">AI</n-button>
            <n-button v-if="p.alerts?.length" size="tiny" text @click="p.alertsOpen=!p.alertsOpen">提醒 {{ p.alerts.length }}</n-button>
            <n-button size="tiny" text @click="openFsVp(p)">量分布</n-button>
            <n-button size="tiny" text @click="openReplay(p)">回放</n-button>
            <n-button size="tiny" text @click="exportFsPanel(p)">导出</n-button>
            <n-button v-if="i > 0" size="tiny" text class="fs-remove" @click="removeFsPanel(i)">移除</n-button>
          </span>
        </div>
        <!-- 完整报价条（与详情页同口径） -->
        <div v-if="p.quote" class="fs-quote">
          <div class="fs-q-row">
            <span class="fs-q-status">{{ p.quote.session_status || '—' }}</span>
            <span v-if="p.quote.is_realtime && p.quote.timestamp" class="fs-q-time">{{ p.quote.timestamp.slice(11) }}</span>
            <span class="fs-q-stat">今开 <b>{{ qfmt(p.quote.open) }}</b></span>
            <span class="fs-q-stat">最高 <b>{{ qfmt(p.quote.high) }}</b></span>
            <span class="fs-q-stat">最低 <b>{{ qfmt(p.quote.low) }}</b></span>
            <span class="fs-q-stat">均价 <b>{{ qfmt(p.quote.avg_price) }}</b></span>
            <span class="fs-q-stat">量比 <b>{{ qfmt(p.quote.volume_ratio) }}</b></span>
            <span class="fs-q-stat">成交额 <b>{{ fmtMoney(p.quote.amount) }}</b></span>
            <span class="fs-q-stat">换手 <b>{{ p.quote.turnover_pct != null ? p.quote.turnover_pct.toFixed(2) + '%' : '—' }}</b></span>
            <span class="fs-q-stat">RVOL <b>{{ fsRvol(p) != null ? fsRvol(p).toFixed(2) + 'x' : '—' }}</b></span>
          </div>
          <div class="fs-q-row" v-if="fsDepth(p) || p.quote.cost_price">
            <span v-if="fsDepth(p)" class="fs-q-stat">盘口
              <b :style="{color: fsDepth(p).imb >= 0 ? UP_COLOR : DOWN_COLOR}">{{ fsDepth(p).imb != null ? (fsDepth(p).imb * 100).toFixed(0) + '%' : '—' }}</b>
              <span style="color:var(--c-text-faint)"> · 价差 {{ fsDepth(p).spread != null ? fsDepth(p).spread.toFixed(1) + 'bp' : '—' }}</span>
            </span>
            <span v-if="p.quote.bids?.[0]" class="fs-q-stat">买一 <b>{{ qfmt(p.quote.bids[0].price) }}</b> × {{ p.quote.bids[0].volume }}</span>
            <span v-if="p.quote.asks?.[0]" class="fs-q-stat">卖一 <b>{{ qfmt(p.quote.asks[0].price) }}</b> × {{ p.quote.asks[0].volume }}</span>
            <span v-if="p.quote.cost_price" class="fs-q-stat">成本 <b>{{ qfmt(p.quote.cost_price) }}</b>
              <span v-if="p.quote.stop_price"> · 止损 <b style="color:var(--c-up)">{{ qfmt(p.quote.stop_price) }}</b></span>
              <span v-if="p.quote.take_profit_price"> · 止盈 <b style="color:var(--c-down)">{{ qfmt(p.quote.take_profit_price) }}</b></span>
            </span>
          </div>
        </div>
        <!-- 栏内 AI 解读：与详情页一致，放在分时图上方 -->
        <div v-if="p.aiOpen" class="fs-ai">
          <div class="fs-ai-head">
            <b>AI 盘面解读</b><span>非预测 · 非投资建议</span>
            <n-button size="tiny" text style="margin-left:auto" @click="closeFsAi(p)">关闭</n-button>
          </div>
          <div v-if="p.aiLoading" class="fs-ai-loading">正在结合行情 / 分钟结构 / 资讯 / 模型信号生成…</div>
          <div v-else-if="p.aiError" class="fs-ai-error">{{ p.aiError }}</div>
          <template v-else-if="p.aiInsight">
            <div class="fs-ai-summary"><b>{{ p.aiInsight.stance }}</b> {{ p.aiInsight.summary }}</div>
            <ul class="fs-ai-list"><li v-for="(x, j) in p.aiInsight.observations" :key="'o'+j">{{ x }}</li></ul>
            <template v-if="p.aiInsight.risks?.length">
              <div class="fs-ai-sub">风险观察</div>
              <ul class="fs-ai-list risk"><li v-for="(x, j) in p.aiInsight.risks" :key="'r'+j">{{ x }}</li></ul>
            </template>
            <template v-if="p.aiInsight.watch?.length">
              <div class="fs-ai-sub">观察价位</div>
              <ul class="fs-ai-list"><li v-for="(x, j) in p.aiInsight.watch" :key="'w'+j">{{ x }}</li></ul>
            </template>
            <div class="fs-ai-foot">{{ p.aiInsight.generated_at }} · {{ p.aiInsight.model }}</div>
          </template>
        </div>
        <div v-if="!p.loading && !p.bars.length" class="fs-panel-empty">无分钟数据</div>
        <div :id="'fsc-' + p.code" class="fs-panel-chart"></div>
        <div v-if="fsLevelChips(p).length" class="fs-levels">
          <span v-for="l in fsLevelChips(p)" :key="l.name + l.value">
            <i :style="{background:l.color}"></i>{{ l.name }} <b>{{ l.value.toFixed(2) }}</b>
          </span>
        </div>
        <!-- 盘中相对强弱：全屏也保留，与详情页同标题/同口径 -->
        <div v-if="fsRsReady(p)" class="fs-rs">
          <div class="fs-rs-head">
            <span class="fs-rs-title">盘中相对强弱</span>
            <span class="fs-rs-sub">个股 vs 沪深300（当日起点=0%，超额=百分点）</span>
          </div>
          <div :id="'fsrs-' + p.code" class="fs-rs-chart"></div>
        </div>
        <!-- 栏内提醒列表 -->
        <div v-if="p.alertsOpen" class="fs-alerts">
          <div class="fs-alerts-head"><b>盘中提醒</b>
            <n-button size="tiny" text style="margin-left:auto" @click="p.alertsOpen=false">关闭</n-button>
          </div>
          <template v-if="p.alerts?.length">
            <div v-for="a in p.alerts.slice(0,6)" :key="a.id" class="fs-alert-item">
              <div class="fs-alert-title">{{ a.title }}</div>
              <div class="fs-alert-body">{{ a.body }}</div>
              <div class="fs-alert-time">{{ (a.created_at || '').slice(11, 16) }}</div>
            </div>
          </template>
          <div v-else class="fs-alert-empty">暂无提醒</div>
        </div>
      </div>
    </div>
    <div v-if="fsPanels.length < 3" class="fs-side fs-side-left" @click="openFsSearch('left')">
      <div class="fs-plus">+</div>
    </div>
    <div v-if="fsPanels.length < 3" class="fs-side fs-side-right" @click="openFsSearch('right')">
      <div class="fs-plus">+</div>
    </div>
    <div v-if="fsSearchOpen" class="fs-search"
      :class="fsSearchSide === 'left' ? 'fs-search-left' : 'fs-search-right'">
      <div class="fs-search-title">添加对比个股（最多 3 栏）</div>
      <StockSuggestInput v-model:value="fsSearchCode" size="small" width="240px" :z-index="4000"
        @select="addFsStock" @enter="addFsStock" />
      <div class="fs-search-hint">代码 / 名称 / 拼音首字母，选中即加入</div>
      <n-button size="tiny" text @click="fsSearchOpen=false">取消</n-button>
    </div>
  </div>

  <!-- 加入分组弹窗：自选+动态组多选，支持就地新建分组并勾选 -->
  <n-modal v-model:show="showAddGroup">
    <n-card style="width:400px;max-width:92vw" title="加入分组" role="dialog" aria-modal="true">
      <n-space vertical size="small">
        <div style="font-size: var(--text-meta);color:var(--c-text-dim)">自选与自定义分组可多选；持仓请到持仓页维护</div>
        <n-checkbox-group v-model:value="pickedGroups">
          <n-space size="small">
            <n-checkbox v-for="g in allGroups" :key="g.id" :value="g.id" :label="g.name" />
          </n-space>
        </n-checkbox-group>
        <div style="display:flex;gap:6px;align-items:center">
          <n-input v-model:value="newGroupName" size="small" placeholder="新分组名称（回车即建）" maxlength="32" style="flex:1" @keyup.enter="createGroupInline" />
          <n-button size="small" :loading="addingGroup" @click="createGroupInline">新增分组</n-button>
        </div>
      </n-space>
      <template #footer>
        <n-space justify="flex-end">
          <n-button size="small" @click="showAddGroup=false">取消</n-button>
          <n-button size="small" type="primary" :loading="savingGroups" @click="saveGroups">保存</n-button>
        </n-space>
      </template>
    </n-card>
  </n-modal>

  <!-- 成交量分布（当日）：POC / VAH / VAL，基于当前周期已加载的 bar -->
  <n-modal v-model:show="showVp" preset="card" :title="vpTitle"
    style="width:560px;max-width:94vw">
    <template v-if="vpData">
      <div ref="vpEl" style="width:100%;height:360px"></div>
      <div class="vp-summary">
        POC <b>{{ vpData.pocPrice.toFixed(2) }}</b>
        · VAH <b>{{ vpData.vah.toFixed(2) }}</b>
        · VAL <b>{{ vpData.val.toFixed(2) }}</b>
        · 总量 <b>{{ vpData.total.toLocaleString() }}</b> 手
      </div>
    </template>
    <n-empty v-else description="当前周期没有可用的分钟量数据" />
  </n-modal>

  <!-- 当日分时回放 -->
  <n-modal v-model:show="showReplay" preset="card" :title="replayTitle"
    style="width:820px;max-width:96vw">
    <template v-if="replayBars.length">
      <div v-if="replayBar" class="replay-meta">
        <b>{{ replayBar.datetime.slice(11) }}</b>
        <span>开 {{ replayBar.open.toFixed(2) }}</span>
        <span>高 {{ replayBar.high.toFixed(2) }}</span>
        <span>低 {{ replayBar.low.toFixed(2) }}</span>
        <span>收 {{ replayBar.close.toFixed(2) }}</span>
        <span>量 {{ replayBar.volume }} 手</span>
      </div>
      <div ref="replayEl" style="width:100%;height:380px"></div>
      <div class="replay-controls">
        <n-button size="small" @click="playReplay">{{ replayPlaying ? '暂停' : '播放' }}</n-button>
        <n-slider v-model:value="replayIdx" :min="1" :max="replayBars.length" :step="1"
          style="flex:1" @update:value="drawReplay" />
        <n-select v-model:value="replaySpeed" size="small" style="width:86px"
          :options="[{label:'慢',value:800},{label:'中',value:300},{label:'快',value:100}]" />
        <span class="replay-idx">{{ replayIdx }}/{{ replayBars.length }}</span>
      </div>
      <div class="replay-hint">只回放当日已加载的 {{ minkPeriod }} bar；拖动滑块或播放。回放不构成交易建议。</div>
    </template>
    <n-empty v-else description="当日没有可回放的分钟数据" />
  </n-modal>

  <!-- 盘中多股对比 -->
  <n-modal v-model:show="showCompare" preset="card" title="盘中多股对比"
    style="width:820px;max-width:96vw">
    <div class="compare-bar">
      <n-input v-model:value="compareInput" size="small"
        placeholder="对比代码，逗号分隔，最多 2 只（如 600519,000001）"
        style="flex:1" @keyup.enter="loadCompare" />
      <n-button size="small" type="primary" :loading="compareLoading" @click="loadCompare">加载</n-button>
    </div>
    <div ref="compareEl" style="width:100%;height:380px"></div>
    <div class="replay-hint">以当日起点=0% 归一化；最多 3 条线（含当前股）。</div>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import IntradayPlaybook from './IntradayPlaybook.vue'
import { ref, computed, watch, onMounted, onBeforeUnmount, onUnmounted, nextTick } from 'vue'
import {NCard, NButton, NInput, NSpace, NSpin, NTag, NEmpty, NDescriptions, NDescriptionsItem, NSelect, NSlider, NButtonGroup, NModal, NCheckboxGroup, NCheckbox, NTabs, NTabPane, useDialog, useMessage} from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { useNavStore } from '../stores/nav'
import { useAuthStore } from '../stores/auth'
import { useThemeStore } from '../stores/theme'
import { useViewport } from '../utils/viewport'
import { addWsListener } from '../utils/ws'
import { chartColors as T } from '../utils/chart'
import StatStrip from './StatStrip.vue'

const props = defineProps({ code: String })
const { isNarrow } = useViewport()
const emit = defineEmits(['back'])
const API = window.location.origin
const _nav = useNavStore()
const dialog = useDialog()
const message = useMessage()
const authStore = useAuthStore()
const _authH = () => authStore.token ? {Authorization: 'Bearer '+authStore.token} : {}
const code = ref(props.code||'')       // 当前页面正在展示的代码
const inputCode = ref(props.code||'')  // 搜索框文本；只有选中/回车才提交为 code
const loading = ref(false)
const loadError = ref('')
const detail = ref(null)
const notFound = ref(false)
const hcnt = ref(0)
let loadSeq = 0
let chartSeq = 0
let alive = true
const _six = v => (String(v || '').match(/\d{6}/) || [''])[0]

function onPickStock(item){
  const c = _six(item?.code) || _six(inputCode.value)
  if(!c) return
  inputCode.value = item?.name ? `${c}  ${item.name}` : c
  code.value = c
  _nav.dcode = c
  _nav.syncHash()
  load()
}

function onQuery(){
  const c = _six(inputCode.value) || _six(code.value)
  if(!c){ message.warning('请输入 6 位代码'); return }
  inputCode.value = c
  code.value = c
  _nav.dcode = c
  _nav.syncHash()
  load()
}

// ── 分组状态（持仓 / 自选 / 动态组）──
function askAi() {
  const name = detail.value?.stock_name || ''
  _nav.askAgent(`帮我分析 ${code.value}${name ? ' ' + name : ''}：结合行情、资金流、最近信号和资讯，给出当前值得关注的点。`)
}

const stockGroups = ref({ groups: [], in_portfolio: false })
const allGroups = ref([])          // 加组弹窗里的全量分组
const pickedGroups = ref([])       // 弹窗勾选
const showAddGroup = ref(false), newGroupName = ref(''), addingGroup = ref(false), savingGroups = ref(false)

async function loadStockGroups(c = code.value, seq = loadSeq){
  if(!c) return
  try{
    const r = await axios.get(API + '/api/watch/stock-groups?code=' + c)
    if(seq !== loadSeq || c !== code.value) return
    stockGroups.value = r.data
  }catch(e){ if(seq === loadSeq && c === code.value) stockGroups.value = { groups: [], in_portfolio: false } }
}
function confirmRemoveGroup(g){
  dialog.warning({
    title: '移除分组',
    content: `是否将该股从「${g.name}」移除？`,
    positiveText: '移除', negativeText: '取消',
    onPositiveClick: async () => {
      const ids = stockGroups.value.groups.map(x => x.id).filter(id => id !== g.id)
      try{
        await axios.put(API + '/api/watch/stock-groups',
          { stock_code: code.value, group_ids: ids }, { headers: _authH() })
        await loadStockGroups()
        message.success(`已移出「${g.name}」`)
      }catch(e){ message.error(e.response?.data?.detail || '移除失败') }
    }
  })
}
async function openAddGroup(){
  showAddGroup.value = true
  newGroupName.value = ''
  pickedGroups.value = stockGroups.value.groups.map(g => g.id)
  try{
    const r = await axios.get(API + '/api/watch/groups')
    allGroups.value = r.data.groups || []
  }catch(e){ allGroups.value = [] }
}
async function createGroupInline(){
  const name = newGroupName.value.trim()
  if (!name) return
  addingGroup.value = true
  try{
    const r = await axios.post(API + '/api/watch/groups', { name }, { headers: _authH() })
    allGroups.value.push({ id: r.data.id, name, is_default: false, count: 0 })
    pickedGroups.value.push(r.data.id)
    newGroupName.value = ''
    message.success(`分组「${name}」已创建并勾选`)
  }catch(e){ message.error(e.response?.data?.detail || '创建失败') }
  addingGroup.value = false
}
async function saveGroups(){
  savingGroups.value = true
  try{
    await axios.put(API + '/api/watch/stock-groups',
      { stock_code: code.value, group_ids: pickedGroups.value }, { headers: _authH() })
    showAddGroup.value = false
    await loadStockGroups()
    message.success('分组已更新')
  }catch(e){ message.error(e.response?.data?.detail || '保存失败') }
  savingGroups.value = false
}
// 复权记忆：记住用户上次选择
const adj = ref(localStorage.getItem('detail_adj') || 'none')
// K 线周期与区间（方案1：区间快捷选择 + 日/周/月切换；days 上限 7000 ≈ 全部历史）
const PERIODS = [{v:'day',t:'日'},{v:'week',t:'周'},{v:'month',t:'月'}]
const RANGES = [{t:'1月',d:30},{t:'1年',d:250},{t:'2年',d:500},{t:'5年',d:1250},{t:'10年',d:2500},{t:'20年',d:4900},{t:'全部',d:7000}]
const period = ref('day')
const range = ref(500)

function switchPeriod(v) { period.value = v; reloadChart() }
function switchRange(d) { range.value = d; reloadChart() }
let lastKline = null
const dateRange = ref('')
const peData = ref([])
const peRange = ref('')
// 上下只：从列表页跳转时写入 localStorage 的最近列表导航上下文
const prevCode = ref(null)
const nextCode = ref(null)
let navList = []
function loadNavList() {
  try { navList = JSON.parse(localStorage.getItem('detail_nav_list') || '[]') || [] } catch (e) { navList = [] }
}
function updateNav() {
  loadNavList()
  prevCode.value = null; nextCode.value = null
  if (!navList.length) return
  const i = navList.indexOf(code.value)
  if (i > 0) prevCode.value = navList[i - 1]
  if (i >= 0 && i < navList.length - 1) nextCode.value = navList[i + 1]
}
function jump(c) { if (c) { _nav.dcode = c; _nav.syncHash(); code.value = c; inputCode.value = c; load() } }
// 供列表页调用：记录当前列表代码序列
window.__setDetailNavList = (codes) => { localStorage.setItem('detail_nav_list', JSON.stringify(codes || [])) }

const fundRows = computed(() => {
  const f = detail.value?.fundamentals
  if (!f || !Object.keys(f).length) return []
  const Y = v => v != null ? fmtMoney(Number(v)) : '—'
  const P = v => v != null ? Number(v).toFixed(2) : '—'
  const B = (v, d = 0) => v != null ? (v / 1e8).toFixed(d) + '亿' : '—'
  return [
    { lbl:'行业', val: f.industry || '—' },
    { lbl:'PE(TTM)', val: f.pe_ttm != null ? P(f.pe_ttm) : '亏损' },
    { lbl:'PE', val: f.pe != null ? P(f.pe) : '—' },
    { lbl:'PB', val: f.pb_mrq != null ? P(f.pb_mrq) : '—' },
    { lbl:'PS(TTM)', val: f.ps_ttm != null ? P(f.ps_ttm) : '—' },
    { lbl:'PS', val: f.ps != null ? P(f.ps) : '—' },
    { lbl:'ROE', val: f.roe != null ? f.roe.toFixed(2) + '%' : '—' },
    { lbl:'营收同比', val: f.revenue_yoy != null ? f.revenue_yoy.toFixed(1) + '%' : '—' },
    { lbl:'净利同比', val: f.profit_yoy != null ? f.profit_yoy.toFixed(1) + '%' : '—' },
    { lbl:'股息率', val: f.dv_ratio != null ? f.dv_ratio.toFixed(2) + '%' : '—' },
    { lbl:'股息TTM', val: f.dv_ttm != null ? f.dv_ttm.toFixed(2) + '%' : '—' },
    { lbl:'换手率', val: f.turnover_rate != null ? f.turnover_rate.toFixed(2) + '%' : '—' },
    { lbl:'量比', val: f.volume_ratio != null ? P(f.volume_ratio) : '—' },
    { lbl:'总市值', val: B(f.market_cap, 2) },
    { lbl:'流通市值', val: B(f.circ_mv, 2) },
    { lbl:'总股本', val: B(f.total_shares, 2) },
    { lbl:'流通股本', val: B(f.float_share, 2) },
    { lbl:'自由流通', val: B(f.free_share, 2) },
    { lbl:'注册资本', val: f.reg_capital != null ? Number(f.reg_capital).toFixed(1) + '万元' : '—' },
    { lbl:'员工', val: f.employees != null ? Y(f.employees) : '—' },
    { lbl:'主营', val: f.main_business || '—' },
  ]
})

// 最新策略信号 → 顶部第 5 张卡；完整理由/多策略明细放 tooltip，不再单独占一整行
const latestSignalCard = computed(() => {
  const d = detail.value
  const list = d?.latest_signals || []
  const date = (d?.latest_signal_date || d?.latest_trade_date || '').slice(5)
  const label = `策略信号${date ? ' · ' + date : ''}`
  if (!list.length) {
    return { label, value: '暂无', color: 'var(--c-text-faint)', sub: '最近交易日无信号', title: '最近交易日无策略信号' }
  }
  const s = list[0]
  const dir = dirName(s.direction)
  const value = s.strength != null ? `${dir} ${s.strength}` : dir
  const color = s.direction === 'buy' ? UP_COLOR : (s.direction === 'sell' ? DOWN_COLOR : 'var(--c-text-faint)')
  const sub = (s.reason || '') + (list.length > 1 ? `  +${list.length - 1}` : '')
  const title = list.map(x => {
    const dd = dirName(x.direction)
    const st = x.strength != null ? ` 强度 ${x.strength}` : ''
    const mv = x.model_version ? ` / ${x.model_version}` : ''
    return `${x.signal_date || ''} ${dd}${st}${mv}\n${x.reason || ''}`
  }).join('\n\n')
  return { label, value, color, sub, title: title || sub }
})

const summaryItems = computed(() => {
  const d = detail.value
  if (!d) return []
  // 盘中优先用实时快照：原来这里固定显示上一交易日收盘，盘中会误导
  const q = quote.value
  const price = (q?.price != null && q.price > 0) ? q.price : d.close
  const chgPct = (q?.price != null && q.price > 0 ? q.change_pct : null) ?? priceChg.value
  const color = chgPct == null ? undefined : (chgPct >= 0 ? UP_COLOR : DOWN_COLOR)
  return [
    { label: d.stock_code, value: d.stock_name },
    { label: '最新价', value: '¥' + (price || 0).toFixed(2) + (chgPct != null ? '  ' + (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '%' : ''), color },
    { label: '数据日期', value: q?.trade_date || d.latest_trade_date },
    { label: '历史信号', value: hcnt.value },
    latestSignalCard.value,
  ]
})

const adjOptions = [
  {value:'none',label:'不复权'},
  {value:'qfq',label:'前复权'},
  {value:'hfq',label:'后复权'},
]
const dirName = d => ({buy:'买入',sell:'卖出',neutral:'中性'}[d]||d)
import { fmtMoney, UP_COLOR, DOWN_COLOR } from '../utils/ui'
const fmt = v => v!=null?fmtMoney(Number(v)):'0'
// 情绪（LLM 三分类）→ 文案/配色：方向域红多绿空，中性灰（design/06 §9.10）
const SENTI_DEF = {1:{t:'多',c:UP_COLOR}, 0:{t:'中',c:'var(--c-text-faint)'}, '-1':{t:'空',c:DOWN_COLOR}}
const sentiOf = s => (s===null||s===undefined) ? null : (SENTI_DEF[s] || null)
const priceChg = ref(null)
const priceColor = ref('#fff')
const hoverInfo = ref(null)  // crosshair hover 时动态更新的行情数据

// ── 详情页扩展图数据（best-effort：失败隐藏卡片不影响主图）──
const mfData = ref([])       // 资金流（万元）
const benchData = ref([])    // 沪深300 同窗 K 线（相对强弱基准）
const marginData = ref([])   // 两融余额（元）
const toplistData = ref([])  // 龙虎榜上榜记录
const chipData = ref(null)   // 筹码分布（现价口径）
const sigData = ref([])      // 模型信号史（含前瞻收益）
const xqData = ref(null)     // 雪球讨论聚合（design/06；mark 语义未定，仅中性展示）
const newsData = ref(null)   // 个股相关资讯（design/07；未总结条目的等级/情绪为 null）
// 右侧栏「雪球讨论 / 相关资讯」做成一个卡片两个 tab；只有一个有数据时自动切到有数据的那页
const sentiTab = ref('xq')
const sentiTabTouched = ref(false)   // 用户手动切过 tab 后，不再被数据到达自动改回
const xqHas = computed(() => !!(xqData.value && (xqData.value.count_7d > 0 || xqData.value.today_count > 0)))
const newsHas = computed(() => !!(newsData.value && newsData.value.count > 0))
function onSentiTabChange(v){ sentiTab.value = v; sentiTabTouched.value = true }
watch([xqHas, newsHas], ([x, n]) => {
  if (sentiTabTouched.value) return
  sentiTab.value = x ? 'xq' : (n ? 'news' : 'xq')
})
// 资讯分类标识色属「质量/状态域」，不表达涨跌方向，故不受红绿铁律约束
const NEWS_CAT_NAME = { flash:'快讯', stock_news:'个股', report:'研报', notice:'公告' }
const NEWS_CAT_COLOR = { flash:'var(--c-info)', stock_news:'var(--c-text-dim)', report:'var(--c-text-dim)', notice:'var(--c-warn)' }
// 资讯情绪（对个股的利好/利空）与雪球帖的看多/看空同属方向域，用同一套红绿、文案各异
const NEWS_SENTI = {1:{t:'利好',c:UP_COLOR}, 0:{t:'中性',c:'var(--c-text-faint)'}, '-1':{t:'利空',c:DOWN_COLOR}}
const newsSentiOf = s => (s===null||s===undefined) ? null : (NEWS_SENTI[s] || null)
// 情绪占比条段：按"已打分帖数"归一（不含未打分帖），0 帖的段不占宽度
const sentiSegs = computed(() => {
  const s = xqData.value?.sentiment
  if (!s || !s.scored) return []
  const tot = s.scored
  return [
    {k:'b', t:'看多', n:s.bull,    w:100*s.bull/tot,    c:UP_COLOR},
    {k:'n', t:'中性', n:s.neutral, w:100*s.neutral/tot, c:'var(--c-text-faint)'},
    {k:'s', t:'看空', n:s.bear,    w:100*s.bear/tot,    c:DOWN_COLOR},
  ].filter(x => x.n > 0)
})
const sigStats = ref(null)   // 信号摘要（侧栏）
const mfCum = ref([])        // 主力净流入累计（与 mfData 同长）

// ── K线组 ↔ PE图 日历窗口联动 ──────────────────────────────────────
// PE 序列是全历史财报日（跨度远大于 K 线窗口），echarts.connect 的百分比
// 联动会把两图对到不同日历时段——所以 PE 不进 connect 组，改按「可见日期
// 区间」双向翻译：主图缩放→日期窗→PE 轴索引→派发 dataZoom（反向亦然）。
let klineDates = []        // 当前 K 线日期轴（drawCharts 每次刷新）
let klineMainChart = null  // c1：派发给它一个，connect 组全员同步
let syncingAx = false      // 派发中标志，切断 事件→派发→事件 回环
const pctIdx = (arr, pct) => Math.min(arr.length - 1, Math.max(0, Math.round((arr.length - 1) * pct / 100)))
// 有序日期数组中找 [>=d0 的首索引, <=d1 的末索引]（窗口越界向内夹）
const dateSpanIdx = (arr, d0, d1) => {
  let i0 = 0, i1 = arr.length - 1
  while (i0 < arr.length && arr[i0] < d0) i0++
  i0 = Math.min(i0, arr.length - 1)   // 窗口整体在对方数据之后时夹到末位，防 start>end
  while (i1 > i0 && arr[i1] > d1) i1--
  return [i0, i1]
}
function syncPeFromKline(){
  const peEl = document.getElementById('c5'); const c5 = peEl && peEl._echart
  if (!c5 || !klineDates.length || !peData.value.length) return
  const dz = klineMainChart?.getOption()?.dataZoom?.[0]
  if (!dz || peData.value.length < 2) return
  const [a, b] = [pctIdx(klineDates, dz.start ?? 0), pctIdx(klineDates, dz.end ?? 100)]
  const [i0, i1] = dateSpanIdx(peData.value.map(d => d.date), klineDates[a], klineDates[b])
  const n = peData.value.length
  syncingAx = true
  try { c5.dispatchAction({ type: 'dataZoom', start: i0 / (n - 1) * 100, end: i1 / (n - 1) * 100 }) } finally { setTimeout(() => { syncingAx = false }) }
}
function syncKlineFromPe(){
  const peEl = document.getElementById('c5'); const c5 = peEl && peEl._echart
  if (!c5 || !klineMainChart || !klineDates.length || !peData.value.length) return
  const dz = c5.getOption()?.dataZoom?.[0]
  if (!dz || klineDates.length < 2) return
  const peDates = peData.value.map(d => d.date)
  const [a, b] = [pctIdx(peDates, dz.start ?? 0), pctIdx(peDates, dz.end ?? 100)]
  const [i0, i1] = dateSpanIdx(klineDates, peDates[a], peDates[b])
  const n = klineDates.length
  syncingAx = true
  try { klineMainChart.dispatchAction({ type: 'dataZoom', start: i0 / (n - 1) * 100, end: i1 / (n - 1) * 100 }) } finally { setTimeout(() => { syncingAx = false }) }
}

// 实例复用：已存在则 setOption 更新（notMerge=true 全量重置 / false 按 series id 合并）
function makeChart(id, opt, notMerge = true){
  const el = document.getElementById(id)
  if(!el) return null
  let c = el._echart
  if(!c){ c = echarts.init(el); el._echart = c }
  c.setOption(opt, { notMerge })
  return c
}

function calcPriceChange(kd){
  if(!kd||!kd.kline||kd.kline.length<2) { priceChg.value=null; priceColor.value='var(--c-text)'; return }
  const kl = kd.kline
  const last = kl[kl.length-1], prev = kl[kl.length-2]
  if(last.close && prev.close){
    priceChg.value = (last.close - prev.close) / prev.close * 100
    priceColor.value = priceChg.value >= 0 ? T.up : T.down
  }
}

async function load(){
  const c = code.value
  if(!c) return
  const seq = ++loadSeq
  loading.value = true
  notFound.value = false
  loadError.value = ''
  detail.value = null
  lastKline = null
  dateRange.value = ''
  quote.value = null        // 换票先清快照，避免旧票价格挂在新票抬头
  quoteError.value = false
  clearTimeout(quoteTimer); quoteTimer = null
  minkPaused.value = false
  minkBars.value = []       // 必须清：否则 5s 快照可能把新票价写进上一只票的最后一根 bar
  minkError.value = false
  minkUpdatedAt.value = null
  minkResetZoom = true      // 新票/查询后回到默认视窗
  benchMink.value = []
  showVp.value = false
  vpData.value = null
  showReplay.value = false
  showCompare.value = false
  if (showFs.value) closeFs()
  intradayAlerts.value = []
  aiReqSeq++; aiLoading.value = false; aiInsight.value = null; aiError.value = ''; aiEvent.value = false
  hoverInfo.value = null
  // 清掉上一只票的全部扩展数据：这些都是 best-effort 补的（allSettled 吞错、空数据隐藏卡片），
  // 不清会在「新票无数据/请求失败」时把上只票的内容留在侧栏与图表——跨股票串数据比空白更难察觉
  xqData.value = null
  newsData.value = null
  sentiTab.value = 'xq'
  sentiTabTouched.value = false
  mfData.value = []; mfCum.value = []
  benchData.value = []
  marginData.value = []
  toplistData.value = []
  chipData.value = null
  sigData.value = []
  sigStats.value = null
  peData.value = []   // 同上：新票 PE 拉空/失败时旧票 PE 图会残留（c5 图层不随 v-if 消失）
  updateNav()
  loadLatestAiEvent(c, seq)   // 通知进入/换页时补展示最近一次事件解读（与主数据并行）

  // detail 与 kline 分开等：K线失败不应把整个详情页打成空白
  const [r1, r2] = await Promise.allSettled([
    axios.get(API+'/api/stock/'+c+'/detail'),
    axios.get(API+'/api/stock/'+c+'/kline?days='+range.value+'&period='+period.value+'&adjust='+adj.value),
  ])
  if(seq !== loadSeq) return
  if(r1.status === 'fulfilled'){
    detail.value = r1.value.data; hcnt.value = r1.value.data.history_count
  } else if(r1.reason?.response?.status === 404){
    notFound.value = true
  } else {
    const status = r1.reason?.response?.status
    loadError.value = (status && status < 500 ? r1.reason?.response?.data?.detail : '')
      || '加载失败，请检查后端服务后重试'
  }
  if(r2.status === 'fulfilled' && r2.value.data?.kline) lastKline = r2.value.data
  loading.value = false
  if(!detail.value) return

  await nextTick()
  drawCharts(lastKline)
  // 主图先起：分时/报价/提醒不等待资金流、雪球、资讯等扩展请求
  loadMink(c)
  loadQuote(c)
  loadIntradayAlerts(c)
  try{
    const r = await axios.get(API+'/api/stock/'+c+'/pe_history')
    if(seq !== loadSeq) return
    if(r.data.data&&r.data.data.length){
      peData.value=r.data.data
      nextTick(()=>{ if(seq === loadSeq) drawPeChart() })
    } else clearPeChart()   // 新票无 PE 数据：清掉上一只票的图与标题区间
  }catch(e){ if(seq === loadSeq) clearPeChart() }

  // ── 扩展图数据（全部 best-effort：空数据隐藏卡片，异常不影响主图）──
  await Promise.allSettled([
    loadStockGroups(c, seq),
    axios.get(API+'/api/stock/'+c+'/moneyflow?days=7000').then(r=>{
      if(seq !== loadSeq) return
      mfData.value = r.data.data||[]
      const cum=[]; let s=0
      mfData.value.forEach(d=>{s+=d.net_main; cum.push(+s.toFixed(0))})
      mfCum.value = cum
    }),
    // 基准与 K 线同窗同周期：相对强弱两条线起点才可比
    // 注意：K线接口返回字段是 trade_date，c8 的 reindex 统一按 date 取，这里先归一化
    axios.get(API+'/api/stock/000300/kline?days='+range.value+'&period='+period.value+'&adjust=none&type=index').then(r=>{
      if(seq !== loadSeq) return
      benchData.value = (r.data.kline||[]).map(x=>({date:x.trade_date, close:x.close}))
    }),
    axios.get(API+'/api/stock/'+c+'/margin?days=7000').then(r=>{ if(seq === loadSeq) marginData.value = r.data.data||[] }),
    axios.get(API+'/api/stock/'+c+'/top_list?days=3650').then(r=>{ if(seq === loadSeq) toplistData.value = r.data.data||[] }),
    axios.get(API+'/api/stock/'+c+'/chip').then(r=>{ if(seq === loadSeq) chipData.value = r.data }),
    axios.get(API+'/api/stock/'+c+'/signals?days=1095').then(r=>{ if(seq === loadSeq) sigData.value = r.data.data||[] }),
    axios.get(API+'/api/stock/'+c+'/xueqiu').then(r=>{ if(seq === loadSeq) xqData.value = r.data }),
    // 相关资讯：min_importance=0 全取（含待总结），重要度交给前端徽标表达
    axios.get(API+'/api/stock/'+c+'/news?days=30&limit=20').then(r=>{ if(seq === loadSeq) newsData.value = r.data }),
  ])
  if(seq !== loadSeq) return
  computeSigStats()
  await nextTick()
  if(seq !== loadSeq) return
  drawAuxCharts(lastKline)
  drawChip()
}

// dataZoom 滑块统一低调样式：ECharts 画在 canvas 上，颜色必须字面量（CSS 变量不生效，
// 会回退默认配色反而刺眼）；填充/手柄/数据剪影全部弱化，滑块只做窗口提示不抢图表主体
const dzSlider = () => ({
  height:16, bottom:4, handleSize:6,
  borderColor:'transparent',
  backgroundColor:T.line,
  fillerColor:T.line,
  dataBackground:{lineStyle:{color:'transparent'},areaStyle:{color:T.line}},
  selectedDataBackground:{lineStyle:{color:T.series[3]},areaStyle:{color:T.line}},
  handleStyle:{color:T.faint,borderColor:T.line},
  moveHandleStyle:{color:T.line},
  textStyle:{color:T.dim,fontSize:11},
  labelStyle:{color:'transparent'},
})

// 联动图组统一 grid：固定像素边距让 11 张图绘图区严格同宽（带右轴的图右边距留在
// 轴标签，无右轴的图留白——对齐优先于零留白）
const GRID_STD = { left:64, right:48, bottom:34 }

function drawCharts(kd){
  if(!kd?.kline?.length){ dateRange.value = ''; return }
  const dates = kd.kline.map(d=>d.trade_date)
  const closes = kd.kline.map(d=>d.close)
  const ohlc = kd.kline.map(d=>[d.open,d.close,d.low,d.high])
  const vols = kd.kline.map(d=>d.volume)
  const amts = kd.kline.map(d=>d.amount)
  const trns = kd.kline.map(d=>d.turnover)
  const bmid = kd.kline.map(d=>d.boll_mid), bup = kd.kline.map(d=>d.boll_upper), blo = kd.kline.map(d=>d.boll_lower)
  const rs = kd.kline.map(d=>d.rsi), di = kd.kline.map(d=>d.dif), de = kd.kline.map(d=>d.dea), ba = kd.kline.map(d=>d.macd_bar)
  // 均线 MA5/10/20/30/60/120/180（前端按 close 计算，前 N-1 点为 null）
  const MA = (n) => closes.map((_, i) => { if (i < n - 1) return null; let s = 0; for (let j = 0; j < n; j++) s += closes[i - j]; return +(s / n).toFixed(2); })
  const ma5 = MA(5), ma10 = MA(10), ma20 = MA(20), ma30 = MA(30), ma60 = MA(60), ma120 = MA(120), ma180 = MA(180)
  const vc = ohlc.map(d=>d[1]>=d[0]?T.up:T.down)
  const bc = ba.map(v=>v>=0?T.up:T.down)
  // 浅灰色网格线（比默认的 --c-border-light 更浅）
  const gl = {lineStyle:{color:T.line}}

  dateRange.value = dates[0]+' ~ '+dates[dates.length-1]
  // 默认显示最近 1 年（约 250 交易日；不足则全显示）
  const totalDays = dates.length
  const SHOW = totalDays <= 250 ? 0 : ((totalDays - 250) / totalDays * 100).toFixed(1)
  const dz = [
    // 滚轮缩放时间窗（普通滚轮=以鼠标为中心缩放，Shift+滚轮=平移）；
    // 五图同组（echarts.connect），任一图滚轮缩放全组同步
    {type:'inside', xAxisIndex:0, zoomOnMouseWheel:true, moveOnMouseWheel:'shift'},
    {type:'slider', xAxisIndex:0, start:SHOW, end:100, ...dzSlider()}
  ]
  const xA = {type:'category',data:dates,axisLabel:{show:false},
    axisLine:{lineStyle:{color:T.line}},
    axisTick:{show:false}}
  const tt = {trigger:'axis',axisPointer:{type:'cross'}}

  // 实例复用：已存在则 setOption(notMerge) 更新，避免每次 dispose+init 卡顿
  function make(id, opt){
    const el = document.getElementById(id)
    if(!el) return null
    let c = el._echart
    if(!c){ c = echarts.init(el); el._echart = c }
    c.setOption(opt, { notMerge: true })
    return c
  }

  const tooltipFmt = p => {
    if(!p||!p.length) return ''
    const d = p[0]; const idx = d.dataIndex; const o = ohlc[idx]
    if(!o) return ''
    const chg = o[1] && ohlc[idx-1] ? ((o[1]-ohlc[idx-1][1])/ohlc[idx-1][1]*100).toFixed(2) : '—'
    const color = chg>=0?T.up:T.down
    const mid = bmid[idx], up = bup[idx], lo = blo[idx]
    // 信号/龙虎榜叠加信息（数据晚于主图到达也不怕：formatter 每次悬停现查 ref）
    const s = sigData.value.find(x=>x.date===dates[idx])
    const t = toplistData.value.find(x=>x.date===dates[idx])
    let extra = ''
    if(s) extra += `<br/><span style="color:${s.direction==='buy'?T.up:T.down}">${s.direction==='buy'?'▲':'▼'}模型信号 ${s.direction==='buy'?'买入':'卖出'} @¥${s.price!=null?s.price.toFixed(2):'—'}</span>`
      + (s.f10d!=null ? ` <span style="font-size: var(--text-meta)">前瞻10d ${(s.f10d*100).toFixed(1)}%</span>` : '')
    if(t) extra += `<br/><span style="color:${T.warn}">◆龙虎榜 ${t.reason||''}${t.net!=null?` 净买入${(t.net/1e4/1e4).toFixed(2)}亿`:''}</span>`
    return `<div style="font-size:12px"><b>${dates[idx]}</b><br/>
      开: ${o[0].toFixed(2)}  收: <span style="color:${color}">${o[1].toFixed(2)}</span> (${chg}%)<br/>
      高: ${o[3].toFixed(2)}  低: ${o[2].toFixed(2)}  量: ${(vols[idx]/1e6).toFixed(1)}M<br/>
      <span style="color:${T.warn}">BOLL上轨: ${up!=null?up.toFixed(2):'—'}</span>
      <span style="color:${T.series[3]}"> 中轨: ${mid!=null?mid.toFixed(2):'—'}</span>
      <span style="color:${T.warn}"> 下轨: ${lo!=null?lo.toFixed(2):'—'}</span>${extra}</div>`
  }

  const c1 = make('c1', {
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:tooltipFmt},
    grid:{...GRID_STD, top:18},
    xAxis:xA, yAxis:{scale:true,splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'K线',type:'candlestick',data:ohlc,itemStyle:{color:T.up,color0:T.down,borderColor:T.up,borderColor0:T.down}},
      {name:'上轨',type:'line',data:bup,lineStyle:{color:T.warn,width:2},symbol:'none',smooth:true},
      {name:'中轨',type:'line',data:bmid,lineStyle:{color:T.series[3],width:2},symbol:'none',smooth:true},
      {name:'下轨',type:'line',data:blo,lineStyle:{color:T.warn,width:2},symbol:'none',smooth:true}
    ]
  })
  // 成交量分位线（P20/P50/P80）：基于**当前显示窗口**计算——回答"今天的天量/
  // 地量在这段行情里处于什么位置"。窗口随 dataZoom（滑块/滚轮/五图联动）变化
  // 时重算；分位用线性插值（对齐 numpy.percentile），零量日（停牌）剔除。
  const quantile = (arr, q) => {
    if (!arr.length) return 0
    const s = [...arr].sort((a, b) => a - b)
    const pos = (s.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
  }
  const volMarkLines = (i0, i1) => {
    const w = vols.slice(i0, i1 + 1).filter(v => v > 0)
    const mk = (q, name, color) => {
      const v = quantile(w, q)
      return { yAxis: v, label: { formatter: `${name} ${(v / 1e6).toFixed(1)}M`, position: 'insideEndTop', fontSize: 11, color },
               lineStyle: { color, type: 'dashed', width: 1 } }
    }
    return { silent: true, symbol: 'none', animation: false,
             data: [mk(0.2, 'P20', T.faint), mk(0.5, 'P50', T.series[3]), mk(0.8, 'P80', T.faint)] }
  }
  const initI0 = Math.max(0, Math.round((totalDays - 1) * (parseFloat(SHOW) || 0) / 100))

  const c2 = make('c2', {
    tooltip:tt, grid:{...GRID_STD, top:8},
    xAxis:xA, yAxis:{axisLabel:{fontSize: 11,formatter:v=>(v/1e6).toFixed(0)+'M'},splitLine:gl},
    dataZoom:dz,
    series:[{name:'量',type:'bar',data:vols,itemStyle:{color:p=>vc[p.dataIndex]},
      markLine: volMarkLines(initI0, totalDays - 1)}]
  })
  // 缩放跟随：五图 connect 联动时任何一图缩放都会改 c2 的窗口，节流后按可见区间重算
  if (c2) {
    let _vqLast = 0
    c2.off('datazoom')
    c2.on('datazoom', () => {
      const now = Date.now()
      if (now - _vqLast < 120) return
      _vqLast = now
      try {
        const d = c2.getOption().dataZoom?.[0]
        if (!d) return
        const i0 = Math.max(0, Math.round((totalDays - 1) * (d.start ?? 0) / 100))
        const i1 = Math.min(totalDays - 1, Math.round((totalDays - 1) * (d.end ?? 100) / 100))
        c2.setOption({ series: [{ markLine: volMarkLines(i0, i1) }] })
      } catch (e) { /* 分位线是增强信息，异常静默不影响主图 */ }
    })
  }
  const c3 = make('c3', {
    tooltip:tt, grid:{...GRID_STD, top:8},
    xAxis:xA, yAxis:{splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'柱',type:'bar',data:ba,itemStyle:{color:p=>bc[p.dataIndex]}},
      {name:'DIF',type:'line',data:di,lineStyle:{color:T.warn,width:1},symbol:'none',smooth:true},
      {name:'DEA',type:'line',data:de,lineStyle:{color:'#06b6d4',width:1},symbol:'none',smooth:true}
    ]
  })
  const c4 = make('c4', {
    tooltip:tt, grid:{...GRID_STD, top:8},
    xAxis:xA, yAxis:{min:0,max:100,splitLine:gl},
    dataZoom:dz,
    series:[{name:'RSI',type:'line',data:rs,lineStyle:{color:T.series[4],width:1.5},symbol:'none',smooth:true,areaStyle:{color:T.series[4],opacity:.12},
      markLine:{silent:true,symbol:'none',data:[{yAxis:70,label:{formatter:'超买'},lineStyle:{color:T.up,type:'dashed'}},{yAxis:30,label:{formatter:'超卖'},lineStyle:{color:T.down,type:'dashed'}}]}}
    ]
  })
  calcPriceChange(kd)
  // crosshair 交互：hover K 线时更新动态行情数据（含成交额/换手）
  if(c1){
    c1.off('mousemove'); c1.off('mouseout')
    c1.on('mousemove', p=>{
      if(p.dataIndex!=null){
        const o = ohlc[p.dataIndex]
        hoverInfo.value = { date: dates[p.dataIndex], open: o[0], close: o[1], low: o[2], high: o[3], volume: vols[p.dataIndex], amount: amts[p.dataIndex] ?? null, turnover: trns[p.dataIndex] ?? null, prevClose: p.dataIndex>0 ? ohlc[p.dataIndex-1][1] : null }
      }
    })
    c1.on('mouseout', ()=>{ hoverInfo.value = null })
  }
  // 均线图（MA5~MA180，与 K 线联动 zoom/十字线；图例可点选显隐——七条线
  // 仅靠颜色难辨，2026-09-15 用户反馈补）
  const c6 = make('c6', {
    tooltip: tt,
    legend:{show:true, top:0, left:'center', itemWidth:14, itemHeight:8, itemGap:6,
            textStyle:{fontSize: 11, color:T.faint}},
    grid:{...GRID_STD, top:26},
    xAxis: { ...xA, axisLabel: { show: true, fontSize: 11, interval: 'auto' } },
    yAxis:{scale:true,splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'MA5',type:'line',data:ma5,lineStyle:{color:T.up,width:1},symbol:'none',smooth:true},
      {name:'MA10',type:'line',data:ma10,lineStyle:{color:T.warn,width:1},symbol:'none',smooth:true},
      {name:'MA20',type:'line',data:ma20,lineStyle:{color:'#06b6d4',width:1},symbol:'none',smooth:true},
      {name:'MA30',type:'line',data:ma30,lineStyle:{color:T.down,width:1},symbol:'none',smooth:true},
      {name:'MA60',type:'line',data:ma60,lineStyle:{color:T.series[4],width:1},symbol:'none',smooth:true},
      {name:'MA120',type:'line',data:ma120,lineStyle:{color:'#ec4899',width:1},symbol:'none',smooth:true},
      {name:'MA180',type:'line',data:ma180,lineStyle:{color:'#64748b',width:1},symbol:'none',smooth:true},
    ]
  })
  const charts = [c1,c2,c3,c4,c6].filter(Boolean)
  if(charts.length){charts.forEach(c=>c.group='s');echarts.connect('s')}
  // K线组 ↔ PE图 日历联动（正向）：监听任一成员缩放（connect 会同步到 c1 的
  // dataZoom），节流后翻译日期窗给 PE 图；重绘（range/period/复权切换）后立即对齐一次
  klineDates = dates
  klineMainChart = c1 || charts[0] || null
  if (klineMainChart) {
    let _axLast = 0
    klineMainChart.off('datazoom')   // drawCharts 每次重绘都会走到，避免处理器累积
    klineMainChart.on('datazoom', () => {
      const now = Date.now()
      if (syncingAx || now - _axLast < 120) return
      _axLast = now
      syncPeFromKline()
    })
    syncPeFromKline()
  }
}

// ── 扩展图（资金流/相对强弱/换手率/两融/ATR + K线信号/龙虎榜叠加）──
// 与主五图共用交易日轴（辅助序列 reindex 到 K 线日期，缺失日 null 断开），
// 进同一 connect 组：任一图缩放全组同步。数据晚于主图到达/换窗重绘均安全。
function drawAuxCharts(kd){
  if(!kd||!kd.kline||!kd.kline.length) return
  const dates = kd.kline.map(d=>d.trade_date)
  const closes = kd.kline.map(d=>d.close)
  const ohlc = kd.kline.map(d=>[d.open,d.close,d.low,d.high])
  const trns = kd.kline.map(d=>d.turnover)
  const idxOf = new Map(dates.map((d,i)=>[d,i]))
  const reindex = (arr, key) => { const m=new Map(arr.map(x=>[x.date,x[key]])); return dates.map(d=>m.has(d)?m.get(d):null) }
  const gl = {lineStyle:{color:T.line}}
  const totalDays = dates.length
  const SHOW = totalDays<=250?0:((totalDays-250)/totalDays*100).toFixed(1)
  const dz = [
    {type:'inside', xAxisIndex:0, zoomOnMouseWheel:true, moveOnMouseWheel:'shift'},
    {type:'slider', xAxisIndex:0, start:SHOW, end:100, ...dzSlider()}
  ]
  const xA = {type:'category',data:dates,axisLabel:{show:false},
    axisLine:{lineStyle:{color:T.line}}, axisTick:{show:false}}
  const tt = {trigger:'axis',axisPointer:{type:'cross'}}
  const newCharts = []

  // c7 资金流：主力净流入柱（万元→亿）+ 累计线（右轴）
  if (mfData.value.length) {
    const nm = reindex(mfData.value, 'net_main')
    const cum = reindex(mfCum.value.map((v,i)=>({date:mfData.value[i].date, v})), 'v')
    const c7 = makeChart('c7', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:[
        {axisLabel:{fontSize: 11,formatter:v=>(v/1e4).toFixed(1)+'亿'},splitLine:gl},
        {axisLabel:{fontSize: 11,formatter:v=>(v/1e4).toFixed(0)+'亿'},splitLine:{show:false}}],
      dataZoom:dz,
      series:[
        {name:'主力净流入',type:'bar',data:nm,itemStyle:{color:p=>(nm[p.dataIndex]??0)>=0?T.up:T.down}},
        {name:'累计(右轴)',type:'line',yAxisIndex:1,data:cum,lineStyle:{color:T.series[3],width:1.5},symbol:'none'}
      ]
    })
    if(c7) newCharts.push(c7)
  }

  // c8 相对强弱：个股 vs 沪深300 归一化（窗口内首个两者皆有数据处=1）+ 超额差
  if (benchData.value.length) {
    const bc = reindex(benchData.value, 'close')
    // 基准缺口前向填充（停牌日个股无行、指数有行 → reindex null）
    let last = null
    for(let i=0;i<bc.length;i++){ if(bc[i]!=null) last=bc[i]; else if(last!=null) bc[i]=last }
    let i0 = 0
    while(i0<dates.length && (closes[i0]==null||bc[i0]==null)) i0++
    if (i0 < dates.length) {
      const b0 = closes[i0], m0 = bc[i0]
      const rel = closes.map(c=>c!=null?+(c/b0).toFixed(4):null)
      const ben = bc.map(c=>c!=null?+(c/m0).toFixed(4):null)
      const exc = rel.map((v,i)=>v!=null&&ben[i]!=null?+(v-ben[i]).toFixed(4):null)
      const c8 = makeChart('c8', {
        tooltip:tt, grid:{...GRID_STD, top:18},
        legend:{show:true,top:0,itemWidth:14,itemHeight:8,itemGap:6,textStyle:{fontSize: 11,color:T.faint}},
        xAxis:xA, yAxis:{scale:true,splitLine:gl,axisLabel:{fontSize: 11}},
        dataZoom:dz,
        series:[
          {name:'个股',type:'line',data:rel,lineStyle:{color:T.series[3],width:1.5},symbol:'none'},
          {name:'沪深300',type:'line',data:ben,lineStyle:{color:T.faint,width:1},symbol:'none'},
          {name:'超额',type:'line',data:exc,lineStyle:{color:T.warn,width:1,type:'dashed'},symbol:'none'}
        ]
      })
      if(c8) newCharts.push(c8)
    }
  }

  // c9 换手率：柱 + P20/50/80 分位线（与成交量图同思路，缩放跟随重算）
  {
    const pctl = (arr,q)=>{ const s=arr.filter(v=>v!=null&&v>0).sort((a,b)=>a-b); if(!s.length) return 0
      const pos=(s.length-1)*q, lo=Math.floor(pos), hi=Math.ceil(pos); return s[lo]+(s[hi]-s[lo])*(pos-lo) }
    const marks = (i0,i1)=>{ const w=trns.slice(i0,i1+1); return {silent:true,symbol:'none',animation:false,
      data:[{yAxis:pctl(w,0.2),label:{formatter:`P20 ${pctl(w,0.2).toFixed(1)}%`,position:'insideEndTop',fontSize: 11,color:T.faint},lineStyle:{color:T.faint,type:'dashed',width:1}},
            {yAxis:pctl(w,0.5),label:{formatter:`P50 ${pctl(w,0.5).toFixed(1)}%`,position:'insideEndTop',fontSize: 11,color:T.series[3]},lineStyle:{color:T.series[3],type:'dashed',width:1}},
            {yAxis:pctl(w,0.8),label:{formatter:`P80 ${pctl(w,0.8).toFixed(1)}%`,position:'insideEndTop',fontSize: 11,color:T.faint},lineStyle:{color:T.faint,type:'dashed',width:1}}]} }
    const initI0 = Math.max(0, Math.round((totalDays-1)*(parseFloat(SHOW)||0)/100))
    const c9 = makeChart('c9', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:{axisLabel:{fontSize: 11,formatter:'{value}%'},splitLine:gl},
      dataZoom:dz,
      series:[{name:'换手率',type:'bar',data:trns,itemStyle:{color:T.series[3]},markLine:marks(initI0,totalDays-1)}]
    })
    if(c9){
      newCharts.push(c9)
      let _tLast = 0
      c9.off('datazoom')
      c9.off('datazoom')
      c9.on('datazoom', ()=>{
        const now=Date.now(); if(now-_tLast<120) return; _tLast=now
        try{
          const d=c9.getOption().dataZoom?.[0]; if(!d) return
          const a=Math.max(0,Math.round((totalDays-1)*(d.start??0)/100)), b=Math.min(totalDays-1,Math.round((totalDays-1)*(d.end??100)/100))
          c9.setOption({series:[{markLine:marks(a,b)}]})
        }catch(e){}
      })
    }
  }

  // c10 两融余额：融资/合计（元→亿），数据稀疏处 connect:'none' 断开不假连线
  if (marginData.value.length > 2) {
    const fin = reindex(marginData.value,'fin'), tot = reindex(marginData.value,'total')
    const c10 = makeChart('c10', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:{axisLabel:{fontSize: 11,formatter:v=>(v/1e8).toFixed(0)+'亿'},splitLine:gl},
      dataZoom:dz,
      series:[
        {name:'融资余额',type:'line',data:fin,lineStyle:{color:T.warn,width:1.5},symbol:'none',connect:'none'},
        {name:'两融合计',type:'line',data:tot,lineStyle:{color:T.series[4],width:1},symbol:'none',connect:'none'}
      ]
    })
    if(c10) newCharts.push(c10)
  }

  // c11 ATR(14)：真实波幅均线 + 占价比%（右轴）——仓位管理视角
  {
    const tr = ohlc.map((o,i)=>{ if(i===0||!o||!ohlc[i-1]) return null
      const pc=ohlc[i-1][1], h=o[3], l=o[2]
      return Math.max(h-l, Math.abs(h-pc), Math.abs(l-pc)) })
    const atr = tr.map((_,i)=>{ if(i<14) return null; let s=0; for(let j=i-13;j<=i;j++) s+=tr[j]; return +(s/14).toFixed(3) })
    const atrPct = atr.map((v,i)=>v!=null&&closes[i]?+(v/closes[i]*100).toFixed(2):null)
    const c11 = makeChart('c11', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:[
        {axisLabel:{fontSize: 11},splitLine:gl,scale:true},
        {axisLabel:{fontSize: 11,formatter:'{value}%'},splitLine:{show:false}}],
      dataZoom:dz,
      series:[
        {name:'ATR14',type:'line',data:atr,lineStyle:{color:T.series[4],width:1.5},symbol:'none'},
        {name:'占价比(右轴)',type:'line',yAxisIndex:1,data:atrPct,lineStyle:{color:T.warn,width:1,type:'dashed'},symbol:'none'}
      ]
    })
    if(c11) newCharts.push(c11)
  }

  // c1 叠加层：模型信号（▲买/▼卖，挂当日低/高点附近——与复权口径无关）+ 龙虎榜（◆橙）
  const c1El = document.getElementById('c1'); const c1 = c1El && c1El._echart
  if (c1) {
    const sigPts = sigData.value.map(s=>{
      const i = idxOf.get(s.date); if(i==null) return null
      const y = s.direction==='buy' ? ohlc[i][2]*0.985 : ohlc[i][3]*1.015
      return {value:[i,y], symbolRotate: s.direction==='buy'?0:180,
              itemStyle:{color: s.direction==='buy'?T.up:T.down}}
    }).filter(Boolean)
    const topPts = toplistData.value.map(t=>{
      const i = idxOf.get(t.date); if(i==null) return null
      return {value:[i, ohlc[i][2]*0.96], itemStyle:{color:T.warn}}
    }).filter(Boolean)
    // 按 id 合并追加（notMerge=false）：不触碰 K 线/BOLL 既有系列，重绘时按 id 原位替换
    c1.setOption({series:[
      {id:'sig_marks', name:'信号', type:'scatter', data:sigPts, symbol:'triangle', symbolSize:9, z:5, tooltip:{show:false}},
      {id:'top_marks', name:'龙虎榜', type:'scatter', data:topPts, symbol:'diamond', symbolSize:7, z:5, tooltip:{show:false}}
    ]}, {notMerge:false})
  }

  // 入联动组（connect 组按组名广播，重复 connect 安全）
  if (newCharts.length) { newCharts.forEach(c=>c.group='s'); echarts.connect('s') }
}

// 侧栏筹码分布（现价口径的前复权价，非时间轴图、不进联动组）
function drawChip(){
  const el = document.getElementById('c12'); const d = chipData.value
  if(!el || !d || !d.buckets || !d.buckets.length) return
  let c = el._echart
  if(!c){ c = echarts.init(el); el._echart = c }
  const prices = d.buckets.map(b=>b.price)
  const vols = d.buckets.map(b=>b.vol/1e8)   // 亿股
  const nearest = prices.reduce((best,p,i)=>Math.abs(p-d.current)<Math.abs(prices[best]-d.current)?i:best,0)
  c.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},textStyle:{fontSize: 11},
      formatter: ps=>{ const p=ps[0]; return `¥${p.name}<br/>成交 ${p.value} 亿股` }},
    grid:{left:6,right:34,top:6,bottom:20,containLabel:true},
    xAxis:{type:'value',axisLabel:{fontSize: 11,formatter:'{value}亿'},splitLine:{lineStyle:{color:T.line}}},
    yAxis:{type:'category',data:prices.map(p=>p.toFixed(2)),axisLabel:{fontSize: 11}},
    series:[{type:'bar',barWidth:'72%',
      // A 股约定红盈绿亏：现价下方=获利盘(红)，上方=套牢盘(绿)
      data:vols.map((v,i)=>({value:v,itemStyle:{color: prices[i]<=d.current?T.up:T.down}})),
      markLine:{silent:true,symbol:'none',
        data:[{yAxis:nearest,label:{formatter:'现价',fontSize: 11,color:T.series[3]},lineStyle:{color:T.series[3],type:'dashed',width:1}}]}}
    ]
  }, {notMerge:true})
}

// 侧栏信号摘要（近 3 年）
function computeSigStats(){
  const d = sigData.value
  if(!d.length){ sigStats.value=null; return }
  const avg = k=>{ const xs=d.filter(x=>x[k]!=null).map(x=>x[k]); return xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:0 }
  sigStats.value = { n:d.length, years:3,
    buys:d.filter(x=>x.direction==='buy').length, sells:d.filter(x=>x.direction==='sell').length,
    f5:avg('f5d'), f10:avg('f10d'), f20:avg('f20d') }
}

// 换股票清掉 c5 画布上的旧 PE 图：c5 块无 v-if（DOM 常驻），只清 peData 不清实例的话
// 旧图像素会一直挂着，看起来像新票的 PE
function clearPeChart(){
  const peEl = document.getElementById('c5')
  if(peEl && peEl._echart) peEl._echart.clear()
  peRange.value = ''
}

function drawPeChart(){
  const peEl = document.getElementById('c5')
  if(!peEl || !peData.value.length) return
  // 实例复用
  let c5 = peEl._echart
  if(!c5){ c5 = echarts.init(peEl); peEl._echart = c5 }
  const peDates = peData.value.map(d=>d.date)
  const peVals = peData.value.map(d=>d.pe_ttm)
  const pbVals = peData.value.map(d=>d.pb_mrq)
  const pctl = peData.value.map(d=>d.pe_percentile)
  peRange.value = peDates[0]+' ~ '+peDates[peDates.length-1]
  c5.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
    legend:{show:true, top:0, left:'center', itemWidth:14, itemHeight:8, itemGap:6,
            textStyle:{fontSize: 11, color:T.faint}},
    grid:{...GRID_STD, top:26},
    xAxis:{type:'category',data:peDates,axisLabel:{show:false},
      axisLine:{lineStyle:{color:T.line}},axisTick:{show:false}},
    yAxis:[
      {type:'value',name:'PE',splitLine:{lineStyle:{color:T.line}}},
      {type:'value',name:'%',min:0,max:100,splitLine:{show:false}}
    ],
    dataZoom:[
      // 滚轮独立缩放保留；窗口与 K 线组经「日历翻译」双向联动（见 syncPe/syncKline），
      // 不直接进 connect 组——两轴日期集不同，百分比联动会对到不同日历时段
      {type:'inside', zoomOnMouseWheel:true, moveOnMouseWheel:'shift'},
      {type:'slider',start:0,end:100,...dzSlider()}
    ],
    series:[
      {name:'PE(TTM)',type:'line',data:peVals,lineStyle:{color:T.series[3],width:1.5},symbol:'none',smooth:true,areaStyle:{color:'rgba(96,165,250,0.1)'}},
      {name:'PB',type:'line',data:pbVals,lineStyle:{color:'#ec4899',width:1},symbol:'none',smooth:true},
      {name:'分位%',type:'line',yAxisIndex:1,data:pctl,lineStyle:{color:T.warn,width:1,type:'dashed'},symbol:'none',smooth:true,
        markLine:{silent:true,symbol:'none',data:[
          {yAxis:30,label:{formatter:'低估30',fontSize: 11,position:'insideEndTop',color:T.down},lineStyle:{color:T.down,type:'dashed',width:1}},
          {yAxis:70,label:{formatter:'高估70',fontSize: 11,position:'insideEndBottom',color:T.up},lineStyle:{color:T.up,type:'dashed',width:1}}]}}
    ]
  })
  // 反向联动：拖 PE 滑块 → 日历窗翻译 → K 线组；绘制完成即按当前 K 线窗口对齐一次
  c5.off('datazoom')
  c5.off('datazoom')
  c5.on('datazoom', () => { if (!syncingAx) syncKlineFromPe() })
  syncPeFromKline()
}

async function reloadChart(){
  const c = code.value
  if(!c) return
  const req = ++chartSeq
  const p = period.value, rng = range.value, a = adj.value
  localStorage.setItem('detail_adj', a)
  try{
    const r = await axios.get(API+'/api/stock/'+c+'/kline?days='+rng+'&period='+p+'&adjust='+a)
    if(req !== chartSeq || c !== code.value) return
    if(r.data?.kline) lastKline = r.data
    // 相对强弱的基准与 K 线同窗同周期，换窗/换周期必须重取（其余扩展序列是长窗 reindex）
    try{
      const rb = await axios.get(API+'/api/stock/000300/kline?days='+rng+'&period='+p+'&adjust=none&type=index')
      if(req === chartSeq && c === code.value)
        benchData.value = (rb.data.kline||[]).map(x=>({date:x.trade_date, close:x.close}))
    }catch(e){}
  }catch(e){ return }
  if(req !== chartSeq || c !== code.value || !lastKline?.kline?.length) return
  await nextTick()
  if(req !== chartSeq) return
  drawCharts(lastKline); drawAuxCharts(lastKline); drawChip()
}

// 窗口 resize 时自适应所有图表
function resizeAll(){
  ['c0','c1','c2','c3','c4','c5','c6','c7','c8','c9','c10','c11','c12','c13'].forEach(id => {
    const el = document.getElementById(id); if (el && el._echart) el._echart.resize()
  })
  if (replayChart) replayChart.resize()
  if (compareChart) compareChart.resize()
  document.querySelectorAll('.fs-panel-chart, .fs-rs-chart').forEach(el => {
    if (el._echart) el._echart.resize()
  })
}

// ── 实时快照（腾讯，服务端 3s 缓存）：盘中 5s，非交易时段 30s ──
// 分钟图只到「最近一根 bar」，报价条补上现价/涨跌幅/量比/五档，盘中不再看昨收。
const quote = ref(null)
const quoteError = ref(false)
const quoteLoading = ref(false)
let quoteTimer = null
let quoteBusy = false
const intradayAlerts = ref([])   // 该股近 8 条盘中提醒（后端 watcher 写 risk_alerts）
let alertTimer = null
const qfmt = v => (v == null || v === 0) ? '—' : Number(v).toFixed(2)
const quotePrice = computed(() => {
  const p = quote.value?.price
  return (p != null && p > 0) ? p : (detail.value?.close ?? null)
})
const quoteChgPct = computed(() => quote.value?.change_pct ?? priceChg.value)
const quoteColor = computed(() => quoteChgPct.value == null ? 'var(--c-text)'
  : (quoteChgPct.value >= 0 ? UP_COLOR : DOWN_COLOR))
const quoteTone = computed(() => {
  if (!quote.value) return quoteError.value ? 'bad' : 'loading'
  if (quote.value.is_realtime) return 'live'
  return quote.value.session_status === '集合竞价' ? 'warn' : 'idle'
})
const quotePriceText = computed(() => quotePrice.value == null ? '—' : Number(quotePrice.value).toFixed(2))
const quoteChgText = computed(() => {
  const q = quote.value
  if (q) {
    if (q.change_pct == null) return ''
    const c = q.change != null ? (q.change >= 0 ? '+' : '') + q.change.toFixed(2) + ' ' : ''
    return c + (q.change_pct >= 0 ? '+' : '') + q.change_pct.toFixed(2) + '%'
  }
  return quoteChgPct.value == null ? '' : (quoteChgPct.value >= 0 ? '+' : '') + quoteChgPct.value.toFixed(2) + '%'
})

async function loadQuote(c = code.value){
  if (quoteBusy || !c) return
  quoteBusy = true; quoteLoading.value = true
  try{
    const r = await axios.get(API+'/api/stock/'+c+'/quote',
      { headers:{'Cache-Control':'no-store'} })
    if (c !== code.value) return          // 切换股票后旧请求返回，丢掉
    quote.value = r.data
    quoteError.value = false
    // 5s 快照只更新最后一根 K 线的 close/high/low，历史 bar 不动；
    // 这样分时图与报价条同步到 5s，而不是等 30s 的 minkline 刷新。
    const last = minkBars.value[minkBars.value.length - 1]
    if (last && r.data.trade_date === last.datetime.slice(0, 10) && r.data.price > 0) {
      last.close = r.data.price
      last.high = Math.max(last.high, r.data.price)
      last.low = Math.min(last.low, r.data.price)
    }
    if (minkBars.value.length && !minkPaused.value) drawMink()
  }catch(e){
    if (c !== code.value) return          // 失败保留旧快照，下轮重试
    quoteError.value = true
  }finally{
    quoteBusy = false; quoteLoading.value = false
    if (alive && code.value) {
      // 旧票请求结束时，新票可能正因 quoteBusy 没能发请求：给新票补一拍
      if (c !== code.value) {
        clearTimeout(quoteTimer)
        quoteTimer = setTimeout(() => loadQuote(), 100)
      } else scheduleQuote()
    }
  }
}
function scheduleQuote(){
  clearTimeout(quoteTimer)
  quoteTimer = setTimeout(loadQuote, quote.value?.is_realtime ? 5000 : 30000)
}

// ── AI 盘面解读（按需点击，服务端 90s 缓存；只解读不预测）──
const aiLoading = ref(false)
const aiInsight = ref(null)
const aiError = ref('')
// 盘中执行卡动作的滚动锚点
const quoteBarEl = ref(null)
const aiCardEl = ref(null)
const alertsPanelEl = ref(null)
const aiEvent = ref(false)      // true = 当前卡片由事件监听自动触发
let aiReqSeq = 0
let aiWsUnwatch = null
const aiStanceClass = computed(() => aiInsight.value?.stance === '偏强' ? 'up'
  : (aiInsight.value?.stance === '偏弱' ? 'down' : 'flat'))

// 事件 AI（后端 worker 完成 → WS intraday_ai）→ 当前股自动展开 AI 卡
function onWsAiEvent(data){
  // 盘中提醒：全局 WS 已推送，详情页直接并入侧栏，别等 30s 轮询
  if (data?.type === 'risk_alert') {
    const incoming = (data.alerts || []).filter(a =>
      a.stock_code === code.value && String(a.kind || '').startsWith('it'))
    if (incoming.length) {
      const seen = new Set()
      intradayAlerts.value = [...incoming, ...intradayAlerts.value]
        .filter(a => { if (seen.has(a.id)) return false; seen.add(a.id); return true })
        .sort((a, b) => b.id - a.id)
        .slice(0, 8)
    }
    return
  }
  if (data?.type !== 'intraday_ai') return
  const ev = (data.events || []).find(e => e.stock_code === code.value)
  if (!ev) return
  aiReqSeq++   // 事件结果优先：让在途的手动 AI 请求作废，避免回来后覆盖事件解读
  if (ev.status === 'done' && ev.result?.ok) {
    aiInsight.value = ev.result
    aiError.value = ''
    aiLoading.value = false
    aiEvent.value = true
  } else if (ev.status === 'failed') {
    aiError.value = ev.error || '事件 AI 解读失败'
    aiLoading.value = false
    aiEvent.value = true
  }
  // 全屏分屏里同一只票也自动展开 AI 面板（否则全屏时事件解读只落在背后页面）
  for (const p of fsPanels.value) {
    if (p.code !== ev.stock_code) continue
    p.aiOpen = true
    p.aiLoading = false
    if (ev.status === 'done' && ev.result?.ok) { p.aiInsight = ev.result; p.aiError = '' }
    else if (ev.status === 'failed') { p.aiError = ev.error || '事件 AI 解读失败' }
    resizeFsPanel(p)
  }
}

// 盘中执行卡：抽屉已由子组件关闭，这里把页面带到对应区域
function playbookScroll(el, delay = 280){
  setTimeout(() => el?.scrollIntoView({ behavior: 'smooth', block: 'center' }), delay)
}

function onPlaybookAction(action){
  if (action === 'alerts') { playbookScroll(alertsPanelEl.value, 260); return }
  if (action === 'ai') {
    if (!aiInsight.value && !aiLoading.value) loadAiInsight()
    nextTick(() => playbookScroll(aiCardEl.value, 280))
    return
  }
  if (action === 'levels') {
    if (!['m1', 'm5', 'm15'].includes(minkPeriod.value)) minkPeriod.value = 'm5'
    showLevels.value = true; showBands.value = true; showOr.value = true; showEvents.value = true
    setTimeout(() => document.getElementById('c0')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 340)
    return
  }
  if (action === 'risk') { playbookScroll(quoteBarEl.value, 260); return }
  if (action === 'ask') askAi()
}

// 从通知/其他页面进入详情时 WS 消息已错过：补拉窗口内最近一次事件解读
async function loadLatestAiEvent(c = code.value, loadToken = null){
  if(!c) return
  const aiSeq = aiReqSeq
  try{
    const r = await axios.get(API+'/api/stock/'+c+'/ai-intraday/latest-event')
    if((loadToken != null && loadToken !== loadSeq) || aiSeq !== aiReqSeq || aiInsight.value) return
    if(r.data?.ok){
      aiInsight.value = r.data
      aiError.value = ''
      aiEvent.value = true
    }
  }catch(e){ /* 无事件/接口失败不打扰详情页 */ }
}

async function loadAiInsight(){
  if(aiLoading.value || !code.value) return
  const c = code.value
  const seq = ++aiReqSeq
  aiLoading.value = true; aiError.value = ''; aiInsight.value = null; aiEvent.value = false
  try{
    const r = await axios.get(API+'/api/stock/'+c+'/ai-intraday')
    if(seq !== aiReqSeq || c !== code.value) return
    if(r.data?.ok) aiInsight.value = r.data
    else aiError.value = r.data?.error || 'AI 解读失败'
  }catch(e){
    if(seq === aiReqSeq && c === code.value)
      aiError.value = e.response?.data?.detail || 'AI 解读失败，请稍后重试'
  }finally{
    if(seq === aiReqSeq) aiLoading.value = false
  }
}
function closeAi(){ aiReqSeq++; aiLoading.value = false; aiInsight.value = null; aiError.value = ''; aiEvent.value = false }

// 盘中提醒列表（仅该股；Chrome 通知走全局 WS，不依赖这个轮询）
async function loadIntradayAlerts(c = code.value){
  if(!c) return
  try{
    const r = await axios.get(API+'/api/risk/alerts?code='+c+'&intraday=true&limit=8')
    if(c === code.value){
      const rows = r.data || []
      const maxId = rows.reduce((m, a) => Math.max(m, a.id || 0), 0)
      // 轮询快照可能早于 WS 推送：保留比快照更新的 WS 行，避免新提醒被旧响应覆盖
      const newer = intradayAlerts.value.filter(a => (a.id || 0) > maxId)
      const seen = new Set()
      intradayAlerts.value = [...rows, ...newer]
        .filter(a => { if (seen.has(a.id)) return false; seen.add(a.id); return true })
        .sort((a, b) => b.id - a.id).slice(0, 8)
    }
  }catch(e){ /* best-effort：提醒接口失败不影响行情 */ }
}

// ── 分钟K线（腾讯实时，design/06 外延）：默认 m5，30s 轮询刷新 ──
const MINK_PERIODS = [{v:'m1',t:'1分'},{v:'m5',t:'5分'},{v:'m15',t:'15分'},{v:'m30',t:'30分'},{v:'m60',t:'60分'}]
const minkPeriod = ref('m5')
const minkBars = ref([])
const minkPaused = ref(false)
const minkError = ref(false)
const minkUpdatedAt = ref(null)
let minkTimer = null
let minkSeq = 0
let minkResetZoom = false

const minkLast = computed(() => minkBars.value[minkBars.value.length - 1] || null)
const minkStatus = computed(() => {
  if (minkPaused.value) return '已暂停'
  if (minkError.value) return '分时数据延迟'
  if (!minkBars.value.length) return minkUpdatedAt.value ? '无分钟数据' : '加载中'
  const q = quote.value
  if (q?.is_realtime && q.timestamp && minkLast.value) {
    // 两边都是北京时间；统一按 UTC 解析，只比较差值
    const ts = s => Date.parse(String(s).replace(' ', 'T') + 'Z')
    if (ts(q.timestamp) - ts(minkLast.value.datetime) > 3 * 60 * 1000) return '分时数据延迟'
  }
  if (minkUpdatedAt.value) {
    const d = minkUpdatedAt.value
    const p = n => String(n).padStart(2, '0')
    return `更新 ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  return '已更新'
})
const minkStatusClass = computed(() => minkPaused.value ? 'paused'
  : (minkError.value || minkStatus.value === '分时数据延迟' ? 'bad' : 'ok'))
const minkLastBarTitle = computed(() => {
  const parts = []
  if (minkLast.value) parts.push(`最近 bar ${minkLast.value.datetime}`)
  if (minkUpdatedAt.value) parts.push(`接口刷新 ${minkUpdatedAt.value.toLocaleTimeString('zh-CN', {hour12: false})}`)
  if (minkError.value) parts.push('最近一次分时请求失败，图可能不是最新')
  return parts.join(' / ') || '分钟数据状态'
})
function toggleMinkPause(){
  minkPaused.value = !minkPaused.value
  if (!minkPaused.value) loadMink()
}

function exportMink(){
  const inst = document.getElementById('c0')?._echart
  if (!inst) return
  const url = inst.getDataURL({type:'png', pixelRatio:2, backgroundColor:T.bg})
  const a = document.createElement('a')
  a.href = url
  a.download = `${code.value}_${minkPeriod.value}_${new Date().toISOString().slice(0,16).replace(/[-:T]/g,'')}.png`
  a.click()
}
function onMinkKey(e){
  if (e.key === 'Escape' && showFs.value) {
    // 弹窗打开时把 Esc 让给弹窗（量分布/回放/对比），不要直接退出全屏
    if (showVp.value || showReplay.value || showCompare.value) return
    if (fsSearchOpen.value) fsSearchOpen.value = false
    else closeFs()
    return
  }
  if (!e.altKey || e.ctrlKey || e.metaKey) return
  const tag = (e.target?.tagName || '').toUpperCase()
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return
  const map = {1:'m1', 2:'m5', 3:'m15', 4:'m30', 5:'m60'}
  const k = e.key
  if (map[k]) {
    e.preventDefault()
    if (minkPeriod.value !== map[k]) minkPeriod.value = map[k]
  } else if (k.toLowerCase() === 'p') {
    e.preventDefault(); toggleMinkPause()
  } else if (k.toLowerCase() === 'f') {
    e.preventDefault(); openFs()
  }
}

// ── 分时辅助层开关 ──
const showLevels = ref(true)
const showBands = ref(true)
const showOr = ref(true)
const showEvents = ref(true)
const minkType = ref('candle')  // candle | line
const minkMaMode = ref('ma')    // ma | ema
const benchMink = ref([])       // 沪深300 同周期分钟线（盘中相对强弱）
const showVp = ref(false)
const vpData = ref(null)
const vpTitle = ref('成交量分布（当日）')
const vpEl = ref(null)
let vpChart = null
const MINK_MIN = { m1: 1, m5: 5, m15: 15, m30: 30, m60: 60 }

const minkLastDay = computed(() => minkBars.value[minkBars.value.length - 1]?.datetime.slice(0, 10) || '')
const minkDayBars = computed(() => {
  const d = minkLastDay.value
  return d ? minkBars.value.filter(b => b.datetime.slice(0, 10) === d) : []
})
const minkRvol = computed(() => {
  const bars = minkDayBars.value
  if (bars.length < 3) return null
  const completed = bars.slice(0, -1)
  const avg = completed.reduce((s, b) => s + (b.volume || 0), 0) / completed.length
  const last = completed[completed.length - 1]
  return avg > 0 ? +(last.volume / avg).toFixed(2) : null
})
const depthStats = computed(() => {
  const q = quote.value
  if (!q?.bids?.length || !q?.asks?.length) return null
  const bidVol = q.bids.reduce((s, b) => s + (b.volume || 0), 0)
  const askVol = q.asks.reduce((s, a) => s + (a.volume || 0), 0)
  const tot = bidVol + askVol
  const b1 = q.bids[0], a1 = q.asks[0]
  return {
    imb: tot ? (bidVol - askVol) / tot : null,
    spread: b1?.price && a1?.price ? +((a1.price / b1.price - 1) * 1e4).toFixed(1) : null,
  }
})

const toMin = hm => { const [h, m] = String(hm).split(':').map(Number); return h * 60 + m }
// 盘中提醒/资讯 → 图上 markPoint（按最近 5 分钟映射到最后一日的 bar）
const eventMarks = computed(() => {
  if (!showEvents.value) return []
  const day = minkLastDay.value
  const dayBars = minkDayBars.value
  if (!day || dayBars.length < 2) return []
  const findBar = ts => {
    const hm = String(ts || '').slice(11, 16)
    if (!hm) return null
    let best = null, bestDiff = Infinity
    for (const b of dayBars) {
      const diff = Math.abs(toMin(b.datetime.slice(11, 16)) - toMin(hm))
      if (diff < bestDiff) { best = b; bestDiff = diff }
    }
    return bestDiff <= 5 ? best : null
  }
  const marks = []
  for (const a of (intradayAlerts.value || [])) {
    if (!String(a.created_at || '').startsWith(day)) continue
    const b = findBar(a.created_at)
    if (b) marks.push({
      coord: [b.datetime.slice(5), b.close], name: a.title || '盘中提醒',
      symbol: 'pin', symbolSize: 18, itemStyle: { color: T.up }, label: { show: false },
    })
  }
  for (const n of (newsData.value?.data || [])) {
    if (!String(n.published_at || '').startsWith(day)) continue
    if (n.importance != null && n.importance < 2) continue
    const b = findBar(n.published_at)
    if (b) marks.push({
      coord: [b.datetime.slice(5), b.close], name: n.title || '资讯',
      symbol: 'circle', symbolSize: 7, itemStyle: { color: T.series[3] }, label: { show: false },
    })
  }
  return marks.slice(0, 14)
})

// 图下价位条：把图上的关键线列成「名称 + 数值」，避免右侧 endLabel 叠字
const minkOrLevels = computed(() => {
  const q = quote.value
  if (!q || !showOr.value || q.trade_date !== minkLastDay.value) return []
  if ((MINK_MIN[minkPeriod.value] || 99) > 15) return []
  const bars = minkDayBars.value
  const step = MINK_MIN[minkPeriod.value] || 5
  const or = mins => {
    const ins = bars.slice(0, Math.max(1, Math.ceil(mins / step)))
    if (!ins.length) return null
    return {hi: Math.max(...ins.map(b => b.high)), lo: Math.min(...ins.map(b => b.low))}
  }
  const o15 = or(15), o30 = or(30), out = []
  if (o15) out.push({name:'OR15高', value:o15.hi, color:T.series[4]},
                     {name:'OR15低', value:o15.lo, color:T.series[4]})
  if (o30) out.push({name:'OR30高', value:o30.hi, color:T.series[5]},
                     {name:'OR30低', value:o30.lo, color:T.series[5]})
  return out
})
const minkLevelChips = computed(() => {
  const q = quote.value
  if (!q || q.trade_date !== minkLastDay.value) return []
  const out = []
  if (showLevels.value) {
    const near = (v, pct) => v != null && q.price && Math.abs(v / q.price - 1) <= pct
    const add = (name, val, color, pct) => {
      if (near(val, pct)) out.push({name, value:+val, color})
    }
    add('昨高', q.prev_high, T.faint, 0.15)
    add('昨低', q.prev_low, T.faint, 0.15)
    add('今开', q.open, T.series[3], 0.08)
    add('成本', q.cost_price, T.warn, 0.12)
    add('止损', q.stop_price, T.up, 0.12)
    add('止盈', q.take_profit_price, T.down, 0.12)
    add('涨停', q.limit_up, T.up, 0.025)
    add('跌停', q.limit_down, T.down, 0.025)
  }
  return out.concat(minkOrLevels.value)
})

async function loadBenchMink(c = code.value, period = minkPeriod.value, minkReq = null){
  if (minkPaused.value || !c) return
  try{
    const r = await axios.get(API+'/api/stock/000300/minkline?period='+period+'&count=320&type=index')
    if((minkReq != null && minkReq !== minkSeq) || period !== minkPeriod.value || c !== code.value) return
    benchMink.value = r.data.bars || []
    await nextTick()
    drawRsChart()
  }catch(e){
    if((minkReq == null || minkReq === minkSeq) && period === minkPeriod.value && c === code.value){
      benchMink.value = []
      await nextTick()
      drawRsChart()
    }
  }
}

function drawRsChart(){
  const el = document.getElementById('c13')
  if (!el) return
  const bars = minkBars.value
  const bm = benchMink.value
  if (!bars.length || !bm.length) {
    if (el._echart) el._echart.clear()
    return
  }
  const day = bars[bars.length - 1].datetime.slice(0, 10)
  const sb = bars.filter(b => b.datetime.slice(0, 10) === day)
  if (sb.length < 2) { if (el._echart) el._echart.clear(); return }
  const bmap = new Map(bm.map(b => [b.datetime, b.close]))
  let last = null
  const bench = sb.map(b => {
    const v = bmap.get(b.datetime)
    if (v != null) last = v
    return last
  })
  let i0 = 0
  while (i0 < sb.length && (sb[i0].close == null || bench[i0] == null)) i0++
  if (i0 >= sb.length - 1) { if (el._echart) el._echart.clear(); return }
  const s0 = sb[i0].close, b0 = bench[i0]
  // 用当日涨跌幅(%) 而非归一化 1：个股/指数日内波动常只有零点几个百分点，
  // 归一化后 y 轴会被「超额=0」撑开，看起来像三条平线。超额单位=百分点。
  const rel = sb.map((b, i) => i >= i0 ? +((b.close / s0 - 1) * 100).toFixed(3) : null)
  const ben = bench.map((v, i) => i >= i0 && v != null ? +((v / b0 - 1) * 100).toFixed(3) : null)
  const exc = rel.map((v, i) => v != null && ben[i] != null ? +(v - ben[i]).toFixed(3) : null)
  makeChart('c13', {
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},
      valueFormatter:v=>v==null?'—':(+v).toFixed(2)+'%'},
    legend:{show:true,top:0,right:4,itemWidth:10,itemHeight:6,
      textStyle:{fontSize:10,color:T.dim}},
    grid:{left:46,right:16,top:18,bottom:18},
    xAxis:{type:'category',data:sb.map(b=>b.datetime.slice(11)),
      axisLabel:{fontSize:10,color:T.faint,hideOverlap:true},
      axisLine:{lineStyle:{color:T.line}}},
    yAxis:{scale:true,splitLine:{lineStyle:{color:T.line,type:'dashed'}},
      axisLabel:{fontSize:10,color:T.faint,formatter:v=>v.toFixed(2)+'%'}},
    series:[
      {name:'个股',type:'line',data:rel,symbol:'none',lineStyle:{color:T.series[3],width:1.2}},
      {name:'沪深300',type:'line',data:ben,symbol:'none',lineStyle:{color:T.faint,width:1}},
      {name:'超额(pp)',type:'line',data:exc,symbol:'none',
        lineStyle:{color:T.warn,width:1,type:'dashed'}},
    ]
  }, true)
}

// ── 成交量分布（Volume Profile / POC / VAH / VAL）──
function computeVolumeProfile(bars){
  if (!bars || bars.length < 2) return null
  const lo = Math.min(...bars.map(b => b.low))
  const hi = Math.max(...bars.map(b => b.high))
  if (!(hi > lo)) return null
  const bins = 30, step = (hi - lo) / bins
  const vols = Array(bins).fill(0)
  for (const b of bars) {
    const tp = (b.high + b.low + b.close) / 3
    const i = Math.min(bins - 1, Math.max(0, Math.floor((tp - lo) / step)))
    vols[i] += b.volume || 0
  }
  const total = vols.reduce((a, b) => a + b, 0)
  if (!total) return null
  const poc = vols.indexOf(Math.max(...vols))
  let i = poc, j = poc, va = vols[poc]
  while (va < total * 0.7 && (i > 0 || j < bins - 1)) {
    const left = i > 0 ? vols[i - 1] : -1
    const right = j < bins - 1 ? vols[j + 1] : -1
    if (right >= left) { j++; va += Math.max(0, right) }
    else { i--; va += Math.max(0, left) }
  }
  return {
    lo, hi, step, bins, vols, poc,
    pocPrice: lo + (poc + 0.5) * step,
    vah: lo + (j + 1) * step, val: lo + i * step,
    vahIdx: j, valIdx: i, total,
  }
}

function openVp(){
  vpTitle.value = '成交量分布 · ' + (detail.value?.stock_name || code.value)
  vpData.value = computeVolumeProfile(minkDayBars.value)
  showVp.value = true
}

function drawVp(){
  const d = vpData.value
  if (!d || !vpEl.value) return
  const labels = Array.from({length: d.bins}, (_, i) => (d.lo + (i + 0.5) * d.step).toFixed(2))
  if (!vpChart) vpChart = echarts.init(vpEl.value)
  vpChart.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},
      formatter(ps){
        const p = ps[0]; if (!p) return ''
        return `${labels[p.dataIndex]}<br/>量 ${Number(p.value).toLocaleString()} 手`
      }},
    grid:{left:70,right:18,top:12,bottom:24},
    xAxis:{type:'value',axisLabel:{fontSize:10,color:T.faint,
      formatter:v=>v>=1e4?(v/1e4).toFixed(1)+'万':v}},
    yAxis:{type:'category',data:labels,axisLabel:{fontSize:10,color:T.faint}},
    series:[{
      type:'bar',data:d.vols,
      itemStyle:{color:p=>p.dataIndex===d.poc?T.up:T.series[3]},
      markLine:{silent:true,symbol:'none',data:[
        {yAxis:labels[d.poc],label:{formatter:`POC ${d.pocPrice.toFixed(2)}`,position:'insideEndTop',fontSize:10,color:T.up},lineStyle:{color:T.up,type:'dashed'}},
        {yAxis:labels[d.vahIdx],label:{formatter:`VAH ${d.vah.toFixed(2)}`,position:'insideEndTop',fontSize:10,color:T.warn},lineStyle:{color:T.warn,type:'dotted'}},
        {yAxis:labels[d.valIdx],label:{formatter:`VAL ${d.val.toFixed(2)}`,position:'insideEndBottom',fontSize:10,color:T.warn},lineStyle:{color:T.warn,type:'dotted'}},
      ]}
    }]
  }, true)
  vpChart.resize()
}

watch(showVp, async v => {
  if (v) { await nextTick(); drawVp() }
  else if (vpChart) { vpChart.dispose(); vpChart = null }
})

// ── 当日分时回放 ──
const showReplay = ref(false)
const replayIdx = ref(1)
const replayPlaying = ref(false)
const replaySpeed = ref(300)
const replayEl = ref(null)
let replayChart = null
let replayTimer = null
const replaySource = ref([])
const replayTitle = ref('分时回放（当日）')
const replayBars = computed(() => replaySource.value)
const replayBar = computed(() => replayBars.value[Math.max(0, replayIdx.value - 1)] || null)

function openReplay(src){
  const panel = src && src.bars ? src : null
  const bars = panel ? fsDayBars(panel) : minkDayBars.value
  if (!bars.length) return
  replaySource.value = bars
  replayTitle.value = '分时回放 · ' + (panel?.name || detail.value?.stock_name || code.value)
  replayIdx.value = Math.min(20, bars.length)
  replayPlaying.value = false
  showReplay.value = true
}

function drawReplay(){
  const el = replayEl.value
  if (!el) return
  const bars = replayBars.value.slice(0, replayIdx.value)
  if (!bars.length) return
  if (!replayChart) replayChart = echarts.init(el)
  const dates = bars.map(b => b.datetime.slice(11))
  const ohlc = bars.map(b => [b.open, b.close, b.low, b.high])
  const vols = bars.map(b => b.volume)
  const vc = ohlc.map(d => d[1] >= d[0] ? T.up : T.down)
  replayChart.setOption({
    axisPointer:{link:[{xAxisIndex:'all'}]},
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},
      formatter(ps){
        const b = bars[ps?.[0]?.dataIndex]
        if (!b) return ''
        return `${b.datetime}<br/>开 ${b.open} 收 <b>${b.close}</b><br/>高 ${b.high} 低 ${b.low}<br/>量 ${b.volume}手`
      }},
    grid:[
      {left:56,right:16,top:12,height:'62%'},
      {left:56,right:16,top:'78%',height:'16%'},
    ],
    xAxis:[
      {type:'category',data:dates,gridIndex:0,axisLabel:{show:false},axisLine:{lineStyle:{color:T.line}}},
      {type:'category',data:dates,gridIndex:1,axisLabel:{fontSize:10,color:T.faint,hideOverlap:true},axisLine:{lineStyle:{color:T.line}}},
    ],
    yAxis:[
      {scale:true,gridIndex:0,splitLine:{lineStyle:{color:T.line}},axisLabel:{fontSize:10,color:T.faint}},
      {gridIndex:1,splitLine:{show:false},axisLabel:{fontSize:10,color:T.faint}},
    ],
    dataZoom:[{type:'inside',xAxisIndex:[0,1],start:0,end:100}],
    series:[
      {name:'分钟K',type:'candlestick',data:ohlc,
        itemStyle:{color:T.up,color0:T.down,borderColor:T.up,borderColor0:T.down}},
      {name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:vols,
        itemStyle:{color:p=>vc[p.dataIndex]}},
    ]
  }, true)
  replayChart.resize()
}

function pauseReplay(){
  replayPlaying.value = false
  if (replayTimer) { clearInterval(replayTimer); replayTimer = null }
}
function playReplay(){
  if (replayPlaying.value) { pauseReplay(); return }
  if (replayIdx.value >= replayBars.value.length) replayIdx.value = 1
  replayPlaying.value = true
  clearInterval(replayTimer)
  replayTimer = setInterval(() => {
    if (!showReplay.value || replayIdx.value >= replayBars.value.length) { pauseReplay(); return }
    replayIdx.value++
    drawReplay()
  }, Math.max(50, +replaySpeed.value))
}
watch(showReplay, async v => {
  if (v) { await nextTick(); drawReplay() }
  else {
    pauseReplay()
    if (replayChart) { replayChart.dispose(); replayChart = null }
  }
})
watch(replaySpeed, () => { if (replayPlaying.value) { pauseReplay(); playReplay() } })

// ── 盘中多股对比 ──
const showCompare = ref(false)
const compareInput = ref('')
const compareLoading = ref(false)
const compareEl = ref(null)
const compareLines = ref([])
let compareChart = null

function openCompare(){
  if (!minkDayBars.value.length) return
  if (!compareInput.value) compareInput.value = '000300'
  showCompare.value = true
  nextTick(() => loadCompare())
}

async function loadCompare(){
  if (!minkDayBars.value.length) return
  const current = {
    code: code.value,
    name: detail.value?.stock_name || code.value,
    bars: minkBars.value,
  }
  const codes = String(compareInput.value || '')
    .split(/[\s,，]+/).map(s => s.trim())
    .filter(c => /^\d{6}$/.test(c) && c !== code.value)
    .filter((c, i, arr) => arr.indexOf(c) === i)
    .slice(0, 2)
  compareLoading.value = true
  try{
    const fetched = await Promise.all(codes.map(async c => {
      try{
        const r = await axios.get(API+'/api/stock/'+c+'/minkline?period='+minkPeriod.value+'&count=320')
        return {code:c, name:c, bars:r.data.bars || []}
      }catch(e){ return {code:c, name:c, bars:[]} }
    }))
    compareLines.value = [current, ...fetched.filter(x => x.bars.length)]
    const missing = fetched.filter(x => !x.bars.length).map(x => x.code)
    if (missing.length) message.warning(`无分钟数据：${missing.join(' / ')}`)
    await nextTick()
    drawCompare()
  } finally {
    compareLoading.value = false
  }
}

function drawCompare(){
  const el = compareEl.value
  const lines = compareLines.value
  if (!el || !lines.length) return
  if (!compareChart) compareChart = echarts.init(el)
  const main = lines[0]
  const day = main.bars[main.bars.length - 1]?.datetime.slice(0, 10)
  const times = main.bars.filter(b => b.datetime.slice(0, 10) === day).map(b => b.datetime.slice(11))
  if (times.length < 2) { compareChart.clear(); return }
  const rows = lines.map(l => {
    const m = new Map(l.bars.filter(b => b.datetime.slice(0, 10) === day)
      .map(b => [b.datetime.slice(11), b.close]))
    return {name: l.name, raw: times.map(t => m.get(t) ?? null)}
  })
  let i0 = 0
  while (i0 < times.length && rows.some(r => r.raw[i0] == null)) i0++
  if (i0 >= times.length - 1) { compareChart.clear(); return }
  const colors = [T.series[3], T.warn, T.series[4]]
  const series = rows.map((r, idx) => {
    const base = r.raw[i0]
    return {
      name: r.name, type: 'line', symbol: 'none',
      data: r.raw.map((v, i) => i >= i0 && v != null ? +((v / base - 1) * 100).toFixed(3) : null),
      lineStyle: {color: colors[idx] || T.series[5], width: idx === 0 ? 1.4 : 1.1},
      connectNulls: false,
    }
  })
  compareChart.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},
      valueFormatter:v=>v==null?'—':(+v).toFixed(2)+'%'},
    legend:{show:true,top:0,right:4,itemWidth:10,itemHeight:6,
      textStyle:{fontSize:10,color:T.dim}},
    grid:{left:52,right:16,top:20,bottom:22},
    xAxis:{type:'category',data:times,axisLabel:{fontSize:10,color:T.faint,hideOverlap:true},
      axisLine:{lineStyle:{color:T.line}}},
    yAxis:{scale:true,splitLine:{lineStyle:{color:T.line,type:'dashed'}},
      axisLabel:{fontSize:10,color:T.faint,formatter:v=>v.toFixed(2)+'%'}},
    series,
  }, true)
  compareChart.resize()
}

watch(showCompare, async v => {
  if (v) { await nextTick(); drawCompare() }
  else if (compareChart) { compareChart.dispose(); compareChart = null }
})

// ── 多栏分时全屏对比（1–3 栏；每栏完整分时功能）──
const showFs = ref(false)
const fsPanels = ref([])
// 全屏共用一份沪深300分钟线：同周期、同一次拉取，避免 3 栏重复请求指数
const fsBenchBars = ref([])
const fsBenchPeriod = ref('')
let fsBenchAt = 0
const fsSearchOpen = ref(false)
const fsSearchSide = ref('left')
const fsSearchCode = ref('')
let fsTimer = null

function newFsPanel(code, name, bars = [], quote = null){
  return {
    code, name, bars, quote, loading: false,
    alerts: [], news: [], alertsOpen: false,
    aiOpen: false, aiLoading: false, aiInsight: null, aiError: '',
  }
}

function fsDayBars(p){
  const bars = p?.bars || []
  const day = bars[bars.length - 1]?.datetime.slice(0, 10)
  return day ? bars.filter(b => b.datetime.slice(0, 10) === day) : []
}

function fsVwap(dayBars){
  const vw = [], u1 = [], l1 = [], u2 = [], l2 = []
  let pv = 0, vv = 0, pv2 = 0
  for (const b of dayBars) {
    const tp = (b.high + b.low + b.close) / 3
    const v = b.volume > 0 ? b.volume : 1
    pv += tp * v; vv += v; pv2 += tp * tp * v
    const mu = pv / vv
    const sd = Math.sqrt(Math.max(0, pv2 / vv - mu * mu))
    vw.push(+mu.toFixed(4))
    u1.push(+(mu + sd).toFixed(4)); l1.push(+(mu - sd).toFixed(4))
    u2.push(+(mu + 2 * sd).toFixed(4)); l2.push(+(mu - 2 * sd).toFixed(4))
  }
  return {vw, u1, l1, u2, l2}
}

function fsOr(dayBars, period = minkPeriod.value){
  const step = MINK_MIN[period] || 5
  const or = mins => {
    const ins = dayBars.slice(0, Math.max(1, Math.ceil(mins / step)))
    if (!ins.length) return null
    return {hi: Math.max(...ins.map(b => b.high)), lo: Math.min(...ins.map(b => b.low))}
  }
  return {o15: or(15), o30: or(30)}
}

function fsRvol(p){
  const bars = fsDayBars(p)
  if (bars.length < 3) return null
  const completed = bars.slice(0, -1)
  const avg = completed.reduce((s, b) => s + (b.volume || 0), 0) / completed.length
  const last = completed[completed.length - 1]
  return avg > 0 ? +(last.volume / avg).toFixed(2) : null
}

function fsDepth(p){
  const q = p?.quote
  if (!q?.bids?.length || !q?.asks?.length) return null
  const bv = q.bids.reduce((s, b) => s + (b.volume || 0), 0)
  const av = q.asks.reduce((s, a) => s + (a.volume || 0), 0)
  const tot = bv + av
  const b1 = q.bids[0], a1 = q.asks[0]
  return {
    imb: tot ? (bv - av) / tot : null,
    spread: b1?.price && a1?.price ? +((a1.price / b1.price - 1) * 1e4).toFixed(1) : null,
  }
}

function fsLevelChips(p){
  const q = p?.quote
  const bars = fsDayBars(p)
  if (!q || !bars.length) return []
  const out = []
  const near = (v, pct) => v != null && q.price && Math.abs(v / q.price - 1) <= pct
  const add = (name, val, color, pct) => { if (near(val, pct)) out.push({name, value: +val, color}) }
  if (showLevels.value) {
    add('昨高', q.prev_high, T.faint, .15)
    add('昨低', q.prev_low, T.faint, .15)
    add('今开', q.open, T.series[3], .08)
    add('成本', q.cost_price, T.warn, .12)
    add('止损', q.stop_price, T.up, .12)
    add('止盈', q.take_profit_price, T.down, .12)
    add('涨停', q.limit_up, T.up, .025)
    add('跌停', q.limit_down, T.down, .025)
  }
  if (showOr.value && (MINK_MIN[minkPeriod.value] || 99) <= 15) {
    const {o15, o30} = fsOr(bars, minkPeriod.value)
    if (o15) out.push({name:'OR15高', value:o15.hi, color:T.series[4]},
                       {name:'OR15低', value:o15.lo, color:T.series[4]})
    if (o30) out.push({name:'OR30高', value:o30.hi, color:T.series[5]},
                       {name:'OR30低', value:o30.lo, color:T.series[5]})
  }
  return out
}

function fsEventMarks(p){
  if (!showEvents.value) return []
  const bars = fsDayBars(p)
  if (bars.length < 2) return []
  const find = ts => {
    const hm = String(ts || '').slice(11, 16)
    if (!hm) return null
    let best = null, bd = Infinity
    for (const b of bars) {
      const d = Math.abs(toMin(b.datetime.slice(11, 16)) - toMin(hm))
      if (d < bd) { best = b; bd = d }
    }
    return bd <= 5 ? best : null
  }
  const marks = []
  for (const a of (p.alerts || [])) {
    const b = find(a.created_at)
    if (b) marks.push({coord:[b.datetime.slice(11), b.close], symbol:'pin', symbolSize:16,
      itemStyle:{color:T.up}, label:{show:false}})
  }
  for (const n of (p.news || [])) {
    if (n.importance != null && n.importance < 2) continue
    const b = find(n.published_at)
    if (b) marks.push({coord:[b.datetime.slice(11), b.close], symbol:'circle', symbolSize:6,
      itemStyle:{color:T.series[3]}, label:{show:false}})
  }
  return marks.slice(0, 14)
}

function fsSessionMarks(bars, q){
  const marks = []
  if (!bars.length || !q) return marks
  const nowHM = q.timestamp ? q.timestamp.slice(11, 16) : ''
  const add = (b, label, pos) => {
    if (!b) return
    const x = b.datetime.slice(11)
    if (marks.some(m => m.xAxis === x)) return
    marks.push({xAxis:x, label:{formatter:label, position:pos, fontSize:8, color:T.faint},
      lineStyle:{color:T.line, type:'dotted', width:1}})
  }
  add(bars[0], '09:30', 'insideStartTop')
  const am = bars.filter(b => b.datetime.slice(11) <= '11:30')
  if (nowHM >= '11:30') add(am[am.length - 1], '11:30', 'insideEndTop')
  const pm = bars.find(b => b.datetime.slice(11) >= '13:00')
  if (nowHM >= '13:00') add(pm, '13:00', 'insideEndBottom')
  if (nowHM >= '15:00') add(bars[bars.length - 1], '15:00', 'insideStartTop')
  return marks
}

function openFs(){
  if (!code.value) return
  fsPanels.value = [newFsPanel(
    code.value,
    detail.value?.stock_name || code.value,
    minkBars.value.slice(),
    quote.value,
  )]
  fsSearchOpen.value = false
  fsSearchCode.value = ''
  showFs.value = true
  nextTick(() => {
    drawFsPanel(fsPanels.value[0])
    loadFsPanel(fsPanels.value[0], true)
  })
  clearInterval(fsTimer)
  fsTimer = setInterval(() => {
    if (!showFs.value) return
    fsPanels.value.forEach(p => loadFsPanel(p, true))
  }, 30000)
}

function closeFs(){
  showFs.value = false
  fsSearchOpen.value = false
  if (fsTimer) { clearInterval(fsTimer); fsTimer = null }
  fsPanels.value.forEach(p => {
    for (const id of ['fsc-', 'fsrs-']) {
      const el = document.getElementById(id + p.code)
      if (el && el._echart) { el._echart.dispose(); el._echart = null }
    }
  })
  fsPanels.value = []
}

async function loadFsBench(){
  const period = minkPeriod.value
  if (fsBenchPeriod.value === period && Date.now() - fsBenchAt < 20000) return
  try {
    const r = await axios.get(API + '/api/stock/000300/minkline?period=' + period + '&count=320&type=index')
    if (period !== minkPeriod.value) return   // 周期已切换，旧响应作废
    fsBenchBars.value = r.data.bars || []
    fsBenchPeriod.value = period
    fsBenchAt = Date.now()
  } catch (e) {
    if (period === minkPeriod.value) fsBenchBars.value = []
  }
}

async function loadFsPanel(p, withQuote = true){
  // 改 ref 数组里的响应式代理，否则名称/价格/加载状态不刷新
  const panel = fsPanels.value.find(x => x.code === p.code) || p
  panel.loading = true
  const period = minkPeriod.value
  const alive = () => fsPanels.value.some(x => x.code === panel.code)
  const rsp = loadFsBench()
  const qp = withQuote
    ? axios.get(API + '/api/stock/' + panel.code + '/quote').then(r => {
        if (alive() && r.data) { panel.quote = r.data; panel.name = r.data.name || panel.name }
      }).catch(() => {})
    : Promise.resolve()
  const bp = axios.get(API + '/api/stock/' + panel.code + '/minkline?period=' + period + '&count=320')
    .then(r => { if (alive() && period === minkPeriod.value) panel.bars = r.data.bars || [] })
    .catch(() => {})
  const ap = withQuote
    ? axios.get(API + '/api/risk/alerts?code=' + panel.code + '&intraday=true&limit=8')
        .then(r => { if (alive()) panel.alerts = r.data || [] }).catch(() => {})
    : Promise.resolve()
  const np = withQuote
    ? axios.get(API + '/api/stock/' + panel.code + '/news?days=1&limit=10')
        .then(r => { if (alive()) panel.news = r.data?.data || [] }).catch(() => {})
    : Promise.resolve()
  await Promise.allSettled([qp, bp, ap, np, rsp])
  if (!alive() || period !== minkPeriod.value) return
  panel.loading = false
  if (!showFs.value) return
  await nextTick()
  drawFsPanel(panel)
}

function drawFsPanel(p){
  const el = document.getElementById('fsc-' + p.code)
  if (!el) return
  let c = el._echart
  if (!c) { c = echarts.init(el); el._echart = c }
  const bars = fsDayBars(p)
  if (!bars.length) { c.clear(); drawFsRs(p); return }

  // 保留用户缩放/图例选择（30s 刷新不重置）
  const prevOpt = c.getOption ? c.getOption() : {}
  const dz = prevOpt?.dataZoom?.[0]
  const zs = dz && dz.start != null ? dz.start : 0
  const ze = dz && dz.end != null ? dz.end : 100
  const lgPrev = prevOpt?.legend?.[0]
  const legendSel = lgPrev?.selected || null

  const dates = bars.map(b => b.datetime.slice(11))
  const ohlc = bars.map(b => [b.open, b.close, b.low, b.high])
  const vols = bars.map(b => b.volume)
  const vc = ohlc.map(d => d[1] >= d[0] ? T.up : T.down)
  const closes = bars.map(b => b.close)
  const ma = n => closes.map((_, i) => i < n ? null : +(closes.slice(i - n + 1, i + 1).reduce((a, b) => a + b, 0) / n).toFixed(2))
  const ema = n => {
    const k = 2 / (n + 1)
    let prev = null
    return closes.map((x, i) => {
      if (i < n - 1) return null
      if (prev == null) prev = closes.slice(0, i + 1).reduce((a, b) => a + b, 0) / (i + 1)
      else prev = x * k + prev * (1 - k)
      return +prev.toFixed(2)
    })
  }
  const q = p.quote
  const vw = fsVwap(bars)
  const line = val => val != null ? bars.map(() => val) : null
  const core = []
  const pushCore = (name, data, color, width = 1, type = 'dashed') => {
    if (!data || !data.some(v => v != null)) return
    core.push({name, type:'line', xAxisIndex:0, yAxisIndex:0, data,
      symbol:'none', connectNulls:false,
      lineStyle:{color, width, type}, tooltip:{show:false}})
  }
  pushCore('昨收', line(q?.prev_close), T.faint)
  pushCore('均价', vw.vw, T.warn, 1.2, 'solid')
  pushCore('现价', line(q?.price), T.series[5], 1.2, 'solid')

  const extra = []
  const pushExtra = (name, data, color, type = 'dashed', width = 1) => {
    if (!data || !data.some(v => v != null)) return
    extra.push({name, type:'line', xAxisIndex:0, yAxisIndex:0, data,
      symbol:'none', connectNulls:false, tooltip:{show:false},
      lineStyle:{color, width, type}})
  }
  if (showBands.value) {
    pushExtra('+1σ', vw.u1, T.warn, 'dashed')
    pushExtra('-1σ', vw.l1, T.warn, 'dashed')
    pushExtra('+2σ', vw.u2, T.warn, 'dotted')
    pushExtra('-2σ', vw.l2, T.warn, 'dotted')
  }
  if (showLevels.value && q) {
    const near = (v, pct = 0.12) => v != null && q.price && Math.abs(v / q.price - 1) <= pct
    const level = (name, val, color, type = 'dashed', width = 1, pct = 0.12) => {
      if (near(val, pct)) pushExtra(name, line(val), color, type, width)
    }
    level('昨高', q.prev_high, T.faint, 'dotted', 1, 0.15)
    level('昨低', q.prev_low, T.faint, 'dotted', 1, 0.15)
    level('今开', q.open, T.series[3], 'dashed', 1, 0.08)
    level('成本', q.cost_price, T.warn, 'solid', 1.2, 0.12)
    level('止损', q.stop_price, T.up, 'solid', 1.2, 0.12)
    level('止盈', q.take_profit_price, T.down, 'dashed', 1, 0.12)
    level('涨停', q.limit_up, T.up, 'dashed', 1, 0.025)
    level('跌停', q.limit_down, T.down, 'dashed', 1, 0.025)
  }
  if (showOr.value && q && (MINK_MIN[minkPeriod.value] || 99) <= 15) {
    const {o15, o30} = fsOr(bars, minkPeriod.value)
    if (o15) {
      pushExtra('OR15高', line(o15.hi), T.series[4], 'dotted')
      pushExtra('OR15低', line(o15.lo), T.series[4], 'dotted')
    }
    if (o30) {
      pushExtra('OR30高', line(o30.hi), T.series[5], 'dotted')
      pushExtra('OR30低', line(o30.lo), T.series[5], 'dotted')
    }
  }
  const marks = fsEventMarks(p)
  const sessionMarks = fsSessionMarks(bars, q)
  const priceExtras = {
    ...(sessionMarks.length ? {markLine:{symbol:'none', silent:true, animation:false, data:sessionMarks}} : {}),
    ...(marks.length ? {markPoint:{data:marks, label:{show:false}}} : {}),
  }
  const priceSeries = minkType.value === 'line'
    ? {name:'分时', type:'line', data:closes, symbol:'none',
       lineStyle:{color:T.series[5], width:1.3},
       areaStyle:{color:T.series[5], opacity:0.05}, ...priceExtras}
    : {name:'分钟K', type:'candlestick', data:ohlc,
       itemStyle:{color:T.up, color0:T.down, borderColor:T.up, borderColor0:T.down}, ...priceExtras}
  const maSeries = minkMaMode.value === 'ema'
    ? [{name:'EMA9', type:'line', data:ema(9), symbol:'none', lineStyle:{color:T.warn, width:1}},
       {name:'EMA21', type:'line', data:ema(21), symbol:'none', lineStyle:{color:T.series[3], width:1}}]
    : [{name:'MA5', type:'line', data:ma(5), symbol:'none', lineStyle:{color:T.warn, width:1}},
       {name:'MA10', type:'line', data:ma(10), symbol:'none', lineStyle:{color:T.series[3], width:1}}]
  const coreNames = core.map(o => o.name)

  c.setOption({
    tooltip:{trigger:'axis', axisPointer:{type:'cross'},
      formatter(ps){
        const i = ps?.[0]?.dataIndex
        const b = bars[i]
        if (!b) return ''
        const vwv = vw.vw[i]
        const dev = vwv ? (b.close / vwv - 1) * 100 : null
        const dayVols = bars.map(x => x.volume || 0)
        const avg = dayVols.length ? dayVols.reduce((a, x) => a + x, 0) / dayVols.length : 0
        const volX = avg ? b.volume / avg : null
        return `${b.datetime}<br/>开 ${b.open} 收 <b>${b.close}</b><br/>高 ${b.high} 低 ${b.low}`
          + `<br/>均价 ${vwv != null ? vwv.toFixed(2) : '—'}`
          + `${dev != null ? '（偏离 ' + (dev >= 0 ? '+' : '') + dev.toFixed(2) + '%）' : ''}`
          + `<br/>量 ${b.volume}手${volX != null ? '（当日均量 ' + volX.toFixed(2) + 'x）' : ''}`
      }},
    ...(coreNames.length ? {legend:{top:0, left:48, itemWidth:8, itemHeight:5,
      textStyle:{fontSize:8, color:T.dim}, data:coreNames,
      ...(legendSel ? {selected: legendSel} : {})}} : {}),
    grid:[
      {left:44, right:12, top:coreNames.length ? 16 : 8, height:coreNames.length ? '52%' : '56%'},
      {left:44, right:12, top:'72%', height:'18%'},
    ],
    xAxis:[
      {type:'category', data:dates, gridIndex:0, axisLabel:{show:false}, axisLine:{lineStyle:{color:T.line}}},
      {type:'category', data:dates, gridIndex:1, axisLabel:{fontSize:8, color:T.faint, hideOverlap:true}, axisLine:{lineStyle:{color:T.line}}},
    ],
    yAxis:[
      {scale:true, gridIndex:0, splitLine:{lineStyle:{color:T.line}}, axisLabel:{fontSize:8, color:T.faint}},
      {gridIndex:1, splitLine:{show:false}, axisLabel:{fontSize:8, color:T.faint}},
    ],
    dataZoom:[{type:'inside', xAxisIndex:[0,1], start:zs, end:ze}],
    series:[priceSeries, ...maSeries, ...core, ...extra,
      {name:'成交量', type:'bar', xAxisIndex:1, yAxisIndex:1, data:vols,
       itemStyle:{color:p => vc[p.dataIndex]}}],
  }, true)
  c.resize()
  setTimeout(() => c.resize(), 60)
  drawFsRs(p)
}

function fsRsReady(p){
  const sb = fsDayBars(p)
  const bm = fsBenchBars.value
  if (sb.length < 2 || !bm.length) return false
  const times = new Set(bm.map(b => b.datetime))
  return sb.filter(b => times.has(b.datetime)).length > 1
}

// 全屏栏内「盘中相对强弱」：与详情页同口径，个股/沪深300 当日起点=0%，超额=百分点
function drawFsRs(p){
  const el = document.getElementById('fsrs-' + p.code)
  if (!el) return
  let c = el._echart
  if (!c) { c = echarts.init(el); el._echart = c }
  const sb = fsDayBars(p)
  const bm = fsBenchBars.value
  if (sb.length < 2 || !bm.length) { c.clear(); return }
  const bmap = new Map(bm.map(b => [b.datetime, b.close]))
  let last = null
  const bench = sb.map(b => {
    const v = bmap.get(b.datetime)
    if (v != null) last = v
    return last
  })
  let i0 = 0
  while (i0 < sb.length && (sb[i0].close == null || bench[i0] == null)) i0++
  if (i0 >= sb.length - 1) { c.clear(); return }
  const s0 = sb[i0].close, b0 = bench[i0]
  if (!s0 || !b0) { c.clear(); return }
  const rel = sb.map((b, i) => i >= i0 ? +((b.close / s0 - 1) * 100).toFixed(3) : null)
  const ben = bench.map((v, i) => i >= i0 && v != null ? +((v / b0 - 1) * 100).toFixed(3) : null)
  const exc = rel.map((v, i) => v != null && ben[i] != null ? +(v - ben[i]).toFixed(3) : null)
  c.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},
      valueFormatter:v=>v==null?'—':(+v).toFixed(2)+'%'},
    legend:{show:true,top:0,right:2,itemWidth:9,itemHeight:5,
      textStyle:{fontSize:9,color:T.dim}},
    grid:{left:44,right:12,top:16,bottom:16},
    xAxis:{type:'category',data:sb.map(b=>b.datetime.slice(11)),
      axisLabel:{fontSize:9,color:T.faint,hideOverlap:true},
      axisLine:{lineStyle:{color:T.line}}},
    yAxis:{scale:true,splitLine:{lineStyle:{color:T.line,type:'dashed'}},
      axisLabel:{fontSize:9,color:T.faint,formatter:v=>v.toFixed(1)+'%'}},
    series:[
      {name:'个股',type:'line',data:rel,symbol:'none',lineStyle:{color:T.series[3],width:1.2}},
      {name:'沪深300',type:'line',data:ben,symbol:'none',lineStyle:{color:T.faint,width:1}},
      {name:'超额(pp)',type:'line',data:exc,symbol:'none',
        lineStyle:{color:T.warn,width:1,type:'dashed'}},
    ]
  }, true)
  c.resize()
}

function openFsSearch(side){
  if (fsPanels.value.length >= 3) return
  fsSearchSide.value = side
  fsSearchCode.value = ''
  fsSearchOpen.value = true
}

function addFsStock(item){
  const c = String(item?.code || fsSearchCode.value || '').trim()
  if (!/^\d{6}$/.test(c)) { message.warning('请输入 6 位代码'); return }
  if (fsPanels.value.some(p => p.code === c)) { message.warning('该股已在对比中'); return }
  if (fsPanels.value.length >= 3) { message.warning('最多 3 栏'); return }
  const p = newFsPanel(c, item?.name || c)
  fsPanels.value.push(p)
  fsSearchOpen.value = false
  fsSearchCode.value = ''
  nextTick(async () => { await loadFsPanel(p, true) })
}

function removeFsPanel(index){
  const p = fsPanels.value[index]
  if (!p) return
  for (const id of ['fsc-', 'fsrs-']) {
    const el = document.getElementById(id + p.code)
    if (el && el._echart) { el._echart.dispose(); el._echart = null }
  }
  fsPanels.value.splice(index, 1)
  if (!fsPanels.value.length) { closeFs(); return }
  nextTick(() => fsPanels.value.forEach(drawFsPanel))
}

function resizeFsPanel(p){
  nextTick(() => {
    for (const id of ['fsc-', 'fsrs-']) {
      const el = document.getElementById(id + p.code)
      el?._echart?.resize()
    }
  })
}

function closeFsAi(p){
  p.aiOpen = false
  resizeFsPanel(p)
}

async function callFsAi(p){
  const panel = fsPanels.value.find(x => x.code === p.code) || p
  panel.aiOpen = true
  panel.aiLoading = true
  panel.aiError = ''
  panel.aiInsight = null
  resizeFsPanel(panel)
  try {
    const r = await axios.get(API + '/api/stock/' + panel.code + '/ai-intraday')
    if (r.data?.ok) panel.aiInsight = r.data
    else panel.aiError = r.data?.error || 'AI 解读失败'
  } catch (e) {
    panel.aiError = e.response?.data?.detail || 'AI 解读失败，请稍后重试'
  } finally {
    panel.aiLoading = false
    resizeFsPanel(panel)
  }
}

function openFsVp(p){
  vpTitle.value = '成交量分布 · ' + (p.name || p.code)
  vpData.value = computeVolumeProfile(fsDayBars(p))
  showVp.value = true
}

function exportFsPanel(p){
  const inst = document.getElementById('fsc-' + p.code)?._echart
  if (!inst) return
  const url = inst.getDataURL({type:'png', pixelRatio:2, backgroundColor:T.bg})
  const a = document.createElement('a')
  a.href = url
  a.download = `${p.code}_${minkPeriod.value}_${new Date().toISOString().slice(0,16).replace(/[-:T]/g,'')}.png`
  a.click()
}

function redrawFs(){
  if (showFs.value) fsPanels.value.forEach(drawFsPanel)
}

watch(minkPeriod, () => {
  if (showFs.value) fsPanels.value.forEach(p => loadFsPanel(p, false))
})
watch([showLevels, showBands, showOr, showEvents], () => {
  if (minkBars.value.length) drawMink()
  redrawFs()
})
watch([minkType, minkMaMode], () => {
  if (minkBars.value.length) drawMink()
  redrawFs()
})
watch(eventMarks, () => {
  if (minkBars.value.length) drawMink()
})

async function loadMink(c = code.value){
  if (minkPaused.value || !c) return
  const period = minkPeriod.value
  const req = ++minkSeq
  try{
    const r = await axios.get(API+'/api/stock/'+c+'/minkline?period='+period+'&count=320')
    if(req !== minkSeq || c !== code.value || period !== minkPeriod.value) return
    minkBars.value = r.data.bars||[]
    minkError.value = false
    minkUpdatedAt.value = new Date()
    drawMink()
    loadBenchMink(c, period, req)
  }catch(e){
    if(req === minkSeq && c === code.value && period === minkPeriod.value) minkError.value = true
  }
}

function drawMink(){
  const bars = minkBars.value
  if(!bars.length){
    const el = document.getElementById('c0')
    if(el && el._echart) el._echart.clear()
    drawRsChart()
    return
  }

  // 30s/5s 重绘不能把用户刚拖的视窗打回默认：先读当前 dataZoom/legend
  const inst = document.getElementById('c0')?._echart
  let zoomStart = 60, zoomEnd = 100, legendSel = null
  if (inst) {
    const opt = inst.getOption()
    const dz = (opt.dataZoom || [])[0]
    if (dz && dz.start != null) { zoomStart = dz.start; zoomEnd = dz.end }
    const lg = (opt.legend || [])[0]
    if (lg && lg.selected) legendSel = lg.selected
  }
  if (minkResetZoom) { zoomStart = 60; zoomEnd = 100; minkResetZoom = false }

  const dates = bars.map(b=>b.datetime.slice(5))            // MM-DD HH:MM
  const ohlc = bars.map(b=>[b.open, b.close, b.low, b.high])
  const vols = bars.map(b=>b.volume)
  // 分时成交量柱：红涨绿跌 per-bar（对齐日线成交量惯例）
  const vc = ohlc.map(d=>d[1]>=d[0]?T.up:T.down)
  const closes = bars.map(b=>b.close)
  const ma = (n) => closes.map((_,i)=> i<n ? null : +(closes.slice(i-n+1,i+1).reduce((a,b)=>a+b,0)/n).toFixed(2))
  const ema = (n) => {
    const k = 2 / (n + 1)
    let prev = null
    return closes.map((c, i) => {
      if (i < n - 1) return null
      if (prev == null) prev = closes.slice(0, i + 1).reduce((a, b) => a + b, 0) / (i + 1)
      else prev = c * k + prev * (1 - k)
      return +prev.toFixed(2)
    })
  }
  const gl = {lineStyle:{color:T.line}}

  // 昨收/均价/现价线只画最近一个交易日：m5/m60 覆盖多日，整段横线会误导
  const q = quote.value
  const lastDay = bars[bars.length-1].datetime.slice(0,10)
  const dayStart = Math.max(0, bars.findIndex(b=>b.datetime.slice(0,10)===lastDay))
  const qSameDay = q && q.trade_date === lastDay ? q : null
  const dayLine = val => (qSameDay && val != null)
    ? bars.map((_,i)=> i>=dayStart ? val : null) : null
  const dayBars = bars.slice(dayStart)
  const padDay = arr => bars.map((_, i) => i >= dayStart ? (arr[i - dayStart] ?? null) : null)
  const core = []
  const pushCore = (name, data, color, width = 1, type = 'dashed') => {
    if (!data || !data.some(v => v != null)) return
    core.push({name, type:'line', xAxisIndex:0, yAxisIndex:0, data,
      symbol:'none', connectNulls:false,
      lineStyle:{color, width, type}, tooltip:{show:false}})
  }
  // VWAP + 标准差带：按当日已加载 bar 逐根计算；末值应接近腾讯 avg_price
  let vwapData = null
  if (dayBars.length >= 2) {
    const vw = [], u1 = [], l1 = [], u2 = [], l2 = []
    let pv = 0, vv = 0, pv2 = 0
    for (const b of dayBars) {
      const tp = (b.high + b.low + b.close) / 3
      const v = b.volume > 0 ? b.volume : 1
      pv += tp * v; vv += v; pv2 += tp * tp * v
      const mu = pv / vv
      const sd = Math.sqrt(Math.max(0, pv2 / vv - mu * mu))
      vw.push(+mu.toFixed(4))
      u1.push(+(mu + sd).toFixed(4)); l1.push(+(mu - sd).toFixed(4))
      u2.push(+(mu + 2 * sd).toFixed(4)); l2.push(+(mu - 2 * sd).toFixed(4))
    }
    vwapData = { vw, u1, l1, u2, l2 }
  }
  pushCore('昨收', dayLine(qSameDay?.prev_close), T.faint)
  if (vwapData) pushCore('均价', padDay(vwapData.vw), T.warn, 1.2, 'solid')
  else pushCore('均价', dayLine(qSameDay?.avg_price), T.warn)
  pushCore('现价', dayLine(qSameDay?.price), T.series[5], 1.2, 'solid')

  // 额外价位/带/开盘区间：不进 legend，用末端 label 标识
  const extra = []
  const pushExtra = (name, data, color, type = 'dashed', width = 1) => {
    if (!data || !data.some(v => v != null)) return
    // 不在右轴叠 endLabel：价位条在图表下方统一列名称+数值，图内只画线
    extra.push({name, type:'line', xAxisIndex:0, yAxisIndex:0, data,
      symbol:'none', connectNulls:false, tooltip:{show:false},
      lineStyle:{color, width, type}})
  }
  if (showBands.value && vwapData) {
    pushExtra('+1σ', padDay(vwapData.u1), T.warn, 'dashed', 1, false)
    pushExtra('-1σ', padDay(vwapData.l1), T.warn, 'dashed', 1, false)
    pushExtra('+2σ', padDay(vwapData.u2), T.warn, 'dotted', 1, false)
    pushExtra('-2σ', padDay(vwapData.l2), T.warn, 'dotted', 1, false)
  }
  if (showLevels.value && qSameDay) {
    // 只画当前价附近的线：成本 144/现价 78 这种 far-away 线会把日内波动压扁
    const near = (v, pct = 0.12) => v != null && qSameDay.price && Math.abs(v / qSameDay.price - 1) <= pct
    const level = (name, val, color, type = 'dashed', width = 1, pct = 0.12) => {
      if (near(val, pct)) pushExtra(name, dayLine(val), color, type, width, true)
    }
    level('昨高', qSameDay.prev_high, T.faint, 'dotted', 1, 0.15)
    level('昨低', qSameDay.prev_low, T.faint, 'dotted', 1, 0.15)
    level('今开', qSameDay.open, T.series[3], 'dashed', 1, 0.08)
    level('成本', qSameDay.cost_price, T.warn, 'solid', 1.2, 0.12)
    level('止损', qSameDay.stop_price, T.up, 'solid', 1.2, 0.12)
    level('止盈', qSameDay.take_profit_price, T.down, 'dashed', 1, 0.12)
    level('涨停', qSameDay.limit_up, T.up, 'dashed', 1, 0.025)
    level('跌停', qSameDay.limit_down, T.down, 'dashed', 1, 0.025)
  }
  if (showOr.value && qSameDay && (MINK_MIN[minkPeriod.value] || 99) <= 15) {
    // 腾讯按 bar 结束时间标记：m15 首根是 09:45，按「根数」切才不漏 OR15
    const step = MINK_MIN[minkPeriod.value] || 5
    const or = mins => {
      const ins = dayBars.slice(0, Math.max(1, Math.ceil(mins / step)))
      if (!ins.length) return null
      return {hi: Math.max(...ins.map(b => b.high)), lo: Math.min(...ins.map(b => b.low))}
    }
    const o15 = or(15), o30 = or(30)
    if (o15) {
      pushExtra('OR15高', dayLine(o15.hi), T.series[4], 'dotted', 1, true)
      pushExtra('OR15低', dayLine(o15.lo), T.series[4], 'dotted', 1, false)
    }
    if (o30) {
      pushExtra('OR30高', dayLine(o30.hi), T.series[5], 'dotted', 1, true)
      pushExtra('OR30低', dayLine(o30.lo), T.series[5], 'dotted', 1, false)
    }
  }
  const marks = showEvents.value ? eventMarks.value : []

  // 开收盘/午休分隔线：只标最后一个交易日，避免多日图重复。
  // 腾讯 m5/m15 首根常是 09:35/13:05，不能等精确时间戳——用首根/末根 + 当前时间门控。
  const sessionMarks = []
  if (qSameDay) {
    const dayBars = bars.slice(dayStart)
    const addMark = (bar, label, pos = 'insideEndTop') => {
      if (!bar) return
      const x = bar.datetime.slice(5)
      if (sessionMarks.some(m => m.xAxis === x)) return
      sessionMarks.push({
        xAxis: x,
        label: {formatter: label, position: pos, fontSize: 9, color: T.faint},
        lineStyle: {color: T.line, type: 'dotted', width: 1},
      })
    }
    const nowHM = q?.timestamp ? q.timestamp.slice(11, 16)
      : (dayBars[dayBars.length - 1]?.datetime.slice(11) || '')
    const amBars = dayBars.filter(b => b.datetime.slice(11) <= '11:30')
    const pmFirst = dayBars.find(b => b.datetime.slice(11) >= '13:00')
    addMark(dayBars[0], '09:30', 'insideStartTop')
    // 11:30 与 13:00 在 category 轴上相邻；一个标签放顶部、一个放底部，避免竖排叠字
    if (nowHM >= '11:30') addMark(amBars[amBars.length - 1], '11:30', 'insideEndTop')
    if (nowHM >= '13:00') addMark(pmFirst, '13:00', 'insideEndBottom')
    if (nowHM >= '15:00') addMark(dayBars[dayBars.length - 1], '15:00', 'insideStartTop')
  }

  const coreNames = core.map(o => o.name)
  const rightPad = 16
  const priceExtras = {
    ...(sessionMarks.length ? {markLine:{symbol:'none',silent:true,animation:false,data:sessionMarks}} : {}),
    ...(marks.length ? {markPoint:{data:marks,label:{show:false}}} : {}),
  }
  const priceSeries = minkType.value === 'line'
    ? {name:'分时', type:'line', xAxisIndex:0, yAxisIndex:0, data:closes,
       symbol:'none', lineStyle:{color:T.series[5],width:1.4},
       areaStyle:{color:T.series[5],opacity:0.06}, ...priceExtras}
    : {name:'分钟K', type:'candlestick', xAxisIndex:0, yAxisIndex:0, data:ohlc,
       itemStyle:{color:T.up,color0:T.down,borderColor:T.up,borderColor0:T.down}, ...priceExtras}
  const maSeries = minkMaMode.value === 'ema'
    ? [{name:'EMA9',type:'line',xAxisIndex:0,yAxisIndex:0,data:ema(9),
        lineStyle:{color:T.warn,width:1},symbol:'none'},
       {name:'EMA21',type:'line',xAxisIndex:0,yAxisIndex:0,data:ema(21),
        lineStyle:{color:T.series[3],width:1},symbol:'none'}]
    : [{name:'MA5',type:'line',xAxisIndex:0,yAxisIndex:0,data:ma(5),
        lineStyle:{color:T.warn,width:1},symbol:'none'},
       {name:'MA10',type:'line',xAxisIndex:0,yAxisIndex:0,data:ma(10),
        lineStyle:{color:T.series[3],width:1},symbol:'none'}]
  makeChart('c0', {
    axisPointer:{link:[{xAxisIndex:'all'}]},
    ...(coreNames.length ? {legend:{top:0,left:72,itemWidth:10,itemHeight:6,
      textStyle:{fontSize:10,color:T.dim},data:coreNames,
      ...(legendSel ? {selected: legendSel} : {})}} : {}),
    grid:[
      {left:66,right:rightPad,top:coreNames.length?20:8,height:coreNames.length?'53%':'56%'},
      {left:66,right:rightPad,top:'72%',height:'22%'},
    ],
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},
      formatter(ps){
        const i = ps?.[0]?.dataIndex
        const b = bars[i]; if(!b) return ''
        const chg = ((b.close-b.open)/b.open*100)
        const di = i - dayStart
        const vw = vwapData && di >= 0 ? vwapData.vw[di] : null
        const vwDev = vw ? (b.close / vw - 1) * 100 : null
        const dayVols = dayBars.map(x => x.volume || 0)
        const avgVol = dayVols.length ? dayVols.reduce((a, x) => a + x, 0) / dayVols.length : 0
        const volX = avgVol ? b.volume / avgVol : null
        return `${b.datetime}<br/>开 ${b.open} 收 <b>${b.close}</b>（${chg>=0?'+':''}${chg.toFixed(2)}%）<br/>高 ${b.high} 低 ${b.low}`
          + `<br/>均价 ${vw != null ? vw.toFixed(2) : '—'}`
          + `${vwDev != null ? '（偏离 ' + (vwDev >= 0 ? '+' : '') + vwDev.toFixed(2) + '%）' : ''}`
          + `<br/>量 ${b.volume}手${volX != null ? '（当日均量 ' + volX.toFixed(2) + 'x）' : ''}`
          + ` / 换手 ${b.turnover_rate_pct!=null?b.turnover_rate_pct+'%':'-'}`
      }},
    xAxis:[
      {type:'category',data:dates,gridIndex:0,axisLabel:{show:false},axisLine:{lineStyle:{color:T.line}}},
      {type:'category',data:dates,gridIndex:1,axisLabel:{fontSize: 11,color:T.faint,hideOverlap:true},axisLine:{lineStyle:{color:T.line}}},
    ],
    yAxis:[
      {scale:true,gridIndex:0,splitLine:gl,axisLabel:{fontSize: 11,color:T.faint,hideOverlap:true}},
      {gridIndex:1,splitLine:{show:false},axisLabel:{fontSize: 11,color:T.faint,formatter:v=>(v/1e3).toFixed(0)+'千手'}},
    ],
    dataZoom:[
      {type:'inside',xAxisIndex:[0,1],start:zoomStart,end:zoomEnd},
      {type:'slider',xAxisIndex:[0,1],start:zoomStart,end:zoomEnd,height:13,bottom:2,borderColor:'transparent',backgroundColor:T.line,fillerColor:T.line,handleSize:'60%'},
    ],
    series:[
      priceSeries,
      ...maSeries,
      ...core,
      ...extra,
      {name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:vols,itemStyle:{color:p=>vc[p.dataIndex]}},
    ]
  }, true)   // notMerge：周期切换后昨收/均价/现价线不能残留上一轮的 series
}

watch(minkPeriod, ()=>{ minkResetZoom = true; loadMink() })

const _theme = useThemeStore()
watch(() => _theme.isDark, () => nextTick(() => {
  drawMink(); drawRsChart(); if (showVp.value) drawVp(); if (showReplay.value) drawReplay(); if (showCompare.value) drawCompare()
  if (!lastKline) return
  drawCharts(lastKline); drawAuxCharts(lastKline); drawChip(); drawPeChart()
}))

onMounted(() => {
  window.addEventListener('resize', resizeAll)
  window.addEventListener('keydown', onMinkKey)
  aiWsUnwatch = addWsListener(onWsAiEvent)
  if(code.value) load()
  minkTimer = setInterval(loadMink, 30000)
  alertTimer = setInterval(loadIntradayAlerts, 30000)
})
onBeforeUnmount(() => {
  // ECharts 实例挂在 DOM 上，必须在 DOM 移除前 dispose（onUnmounted 时已经查不到节点）
  if (showFs.value) closeFs()
  ;['c0','c1','c2','c3','c4','c5','c6','c7','c8','c9','c10','c11','c12','c13'].forEach(id => {
    const el = document.getElementById(id)
    if (el && el._echart) { el._echart.dispose(); el._echart = null }
  })
})
onUnmounted(() => {
  alive = false
  window.removeEventListener('resize', resizeAll)
  window.removeEventListener('keydown', onMinkKey)
  if (aiWsUnwatch) { aiWsUnwatch(); aiWsUnwatch = null }
  if (minkTimer) clearInterval(minkTimer)
  if (quoteTimer) clearTimeout(quoteTimer)
  if (alertTimer) clearInterval(alertTimer)
  if (vpChart) { vpChart.dispose(); vpChart = null }
  pauseReplay()
  if (replayChart) { replayChart.dispose(); replayChart = null }
  if (compareChart) { compareChart.dispose(); compareChart = null }
})

watch(()=>props.code, v=>{
  if(v && v!==code.value){
    clearTimeout(quoteTimer); quote.value=null; quoteError.value=false
    code.value=v; inputCode.value=v; load()
  }
})
</script>

<style>
/* 详情页分组标签：✕ 平时隐去，hover 才出现（naive-ui n-tag closable 默认常显） */
.dt-grp-tag .n-tag__close { opacity: 0; transition: opacity .15s; }
.dt-grp-tag:hover .n-tag__close { opacity: 1; }

/* 右侧栏：独立粘性滚动区，页面高度由左侧图表决定 */
.detail-sidebar {
  position: sticky;
  top: 8px;
  align-self: flex-start;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 4px;
}
.detail-sidebar::-webkit-scrollbar { width: 6px; }
.side-panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 12px;
}
.side-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.side-title .nav-svg { color: var(--muted); }
.side-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.side-table tr { background: transparent !important; }
.side-table td {
  border: 0 !important;
  border-bottom: 1px solid var(--c-border-light) !important;
  padding: 6px 4px !important;
}
.side-table tr:last-child td { border-bottom: 0 !important; }
.side-table td:first-child {
  width: 85px;
  color: var(--c-text-dim) !important;
  font-size: var(--text-meta) !important;
  white-space: nowrap;
}
.side-item { padding: 8px 0; border-top: 1px solid var(--c-border-light); }
.side-item:first-child { padding-top: 0; border-top: 0; }
.side-well { margin-bottom: 8px; padding: 8px 10px; background: var(--surface-2); border-radius: 6px; }
.side-more { margin-top: 8px; font-size: var(--text-meta); color: var(--c-text-faint); }
@media (max-width: 1100px) {
  .detail-sidebar { position: static; max-height: none; overflow: visible; padding-right: 0; }
}

/* 侧栏文本块（雪球热帖 / 相关资讯标题与摘要）：两行截断，全文 hover title */
.side-text {
  font-size: var(--text-meta); line-height: 1.55; color: var(--c-text);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; word-break: break-word;
}

/* 实时报价条（分钟图上方）
   左：价格块（kicker/大价/涨跌/状态徽标/更新时间）
   中：7 个等宽统计格（标签在上、数值在下，列与列严格对齐）
   右：刷新频率 + 刷新 + AI
   下：五档或降级提示 */
.quote-bar {
  display: grid;
  grid-template-columns: minmax(215px, auto) minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px 20px;
  padding: 8px 12px 7px; margin-bottom: 6px;
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-card-bg-hover);
}
.quote-bar.quote-off { border-style: dashed; }
.quote-quote {
  grid-column: 1; display: flex; flex-direction: column; gap: 2px;
  min-width: 0;
}
.quote-kicker {
  display: flex; align-items: center; gap: 4px;
  font-size: var(--text-meta); color: var(--c-text-faint);
  line-height: 1.2;
}
.quote-help {
  display: inline-flex; align-items: center; justify-content: center;
  width: 14px; height: 14px; border-radius: 50%;
  border: 1px solid var(--c-border); color: var(--c-text-faint);
  font-size: 9px; line-height: 1; cursor: help; flex-shrink: 0;
}
.quote-price-line {
  display: flex; align-items: baseline; gap: 8px;
  flex-wrap: wrap; min-width: 0;
}
.quote-price {
  font-size: 28px; font-weight: 650; line-height: 1.0;
  font-variant-numeric: tabular-nums; letter-spacing: -.02em;
}
.quote-chg {
  font-size: 15px; font-weight: 600;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.quote-status {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 1px 7px; border-radius: 999px;
  font-size: var(--text-meta); font-weight: 500;
  white-space: nowrap; line-height: 1.4;
}
.quote-status i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.quote-status.live {
  color: var(--c-up);
  background: color-mix(in srgb, var(--c-up) 10%, transparent);
}
.quote-status.live i {
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--c-up) 14%, transparent);
  animation: quotePulse 2s ease-in-out infinite;
}
.quote-status.warn { color: var(--c-warning); background: color-mix(in srgb, var(--c-warning) 10%, transparent); }
.quote-status.idle { color: var(--c-text-dim); background: var(--c-card-bg); border: 1px solid var(--c-border); }
.quote-status.bad { color: var(--c-error); background: color-mix(in srgb, var(--c-error) 9%, transparent); }
.quote-status.loading { color: var(--c-text-faint); background: var(--c-card-bg); border: 1px solid var(--c-border); }
@keyframes quotePulse { 0%, 100% { opacity: 1 } 50% { opacity: .35 } }
.quote-subline {
  display: flex; align-items: center; gap: 6px;
  min-height: 18px; font-size: var(--text-meta);
  color: var(--c-text-faint); line-height: 1.2; white-space: nowrap;
}
.quote-stats {
  grid-column: 2;
  display: grid;
  grid-template-columns: repeat(8, minmax(48px, auto));
  gap: 4px 14px;
  align-items: center;
}
.quote-stat { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.quote-stat-label {
  font-size: var(--text-meta); color: var(--c-text-faint);
  line-height: 1.2; white-space: nowrap;
}
.quote-stat-value {
  font-size: 13px; font-weight: 600; color: var(--c-text);
  line-height: 1.2; font-variant-numeric: tabular-nums; white-space: nowrap;
}
.quote-actions {
  grid-column: 3; display: flex; align-items: center; gap: 8px;
  justify-self: end; white-space: nowrap;
}
.quote-depth {
  grid-column: 1 / -1; display: flex; align-items: baseline;
  flex-wrap: wrap; gap: 4px 16px;
  padding-top: 5px; border-top: 1px solid var(--c-border-light);
  font-size: var(--text-meta); font-variant-numeric: tabular-nums;
}
.quote-depth-label { color: var(--c-text-faint); }
.quote-depth .bid b { color: var(--c-up); font-weight: 600; }
.quote-depth .ask b { color: var(--c-down); font-weight: 600; }
.quote-depth-stat { color: var(--c-text-dim); }
.quote-depth-stat b { font-weight: 600; font-variant-numeric: tabular-nums; }
.quote-depth-stat .qd-muted { color: var(--c-text-faint); margin-left: 3px; }

/* 分时图下方的价位条：图上只画线，名称/数值在这里统一列 */
.mink-levels {
  display: flex; flex-wrap: wrap; gap: 2px 12px;
  margin: -2px 0 2px; font-size: var(--text-meta); color: var(--c-text-dim);
}
.mink-levels i {
  display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  margin-right: 4px; vertical-align: 1px;
}
.mink-levels b { color: var(--c-text); font-weight: 600; font-variant-numeric: tabular-nums; }

/* 分时工具栏 */
.mink-toolbar {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin: 0 0 6px;
}
.mink-p3 {
  font-size: var(--text-meta); color: var(--c-text-faint);
  border: 1px dashed var(--c-border); border-radius: 3px;
  padding: 0 5px; white-space: nowrap; cursor: help;
}

/* 成交量分布弹窗 */
.vp-summary {
  margin-top: 6px; font-size: var(--text-meta); color: var(--c-text-dim);
  display: flex; gap: 10px; flex-wrap: wrap;
}
.vp-summary b { color: var(--c-text); font-variant-numeric: tabular-nums; }

/* 分时回放 */
.replay-meta {
  display: flex; gap: 12px; flex-wrap: wrap;
  font-size: var(--text-meta); color: var(--c-text-dim); margin-bottom: 4px;
}
.replay-meta b { color: var(--c-text); }
.replay-controls { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.replay-idx { font-size: var(--text-meta); color: var(--c-text-faint); font-variant-numeric: tabular-nums; }
.replay-hint { margin-top: 6px; font-size: var(--text-meta); color: var(--c-text-faint); }
.compare-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.quote-hint {
  grid-column: 1 / -1; font-size: var(--text-meta);
  line-height: 1.5; color: var(--c-text-faint);
}
@media (max-width: 1200px) {
  .quote-stats { grid-template-columns: repeat(4, minmax(52px, auto)); }
}
@media (max-width: 900px) {
  .quote-bar { grid-template-columns: minmax(0, 1fr) auto; }
  .quote-stats { grid-column: 1 / -1; }
  .quote-actions { grid-column: 2; grid-row: 1; }
}
@media (max-width: 720px) {
  .quote-price { font-size: 24px; }
  .quote-bar { gap: 6px 12px; }
}

/* 盘中提醒列表（侧栏）：critical 红点 / warn 琥珀点 */
.ia-list { display: flex; flex-direction: column; gap: 6px; }
.ia-item {
  padding: 6px 8px; border-radius: 4px;
  background: var(--c-card-bg-hover); border: 1px solid var(--c-border-light);
}
.ia-head { display: flex; align-items: center; gap: 5px; font-size: var(--text-meta); }
.ia-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.ia-dot.crit { background: var(--c-error); }
.ia-dot.warn { background: var(--c-warning); }
.ia-title {
  flex: 1; color: var(--c-text); font-weight: 500;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ia-time { color: var(--c-text-faint); font-variant-numeric: tabular-nums; flex-shrink: 0; }
.ia-body { margin-top: 2px; font-size: var(--text-meta); line-height: 1.5; color: var(--c-text-dim); }

/* 分钟图状态（暂停/延迟/更新时间） */
.mink-refresh {
  font-size: var(--text-meta); color: var(--c-text-faint);
  min-width: 76px; text-align: right; white-space: nowrap;
}
.mink-refresh::before {
  content: '●'; font-size: 8px; margin-right: 3px; color: var(--c-text-faint);
}
.mink-refresh.on { color: var(--c-up); }
.mink-refresh.on::before {
  color: var(--c-up);
  animation: quotePulse 2s ease-in-out infinite;
}
.mink-status { font-size: var(--text-meta); white-space: nowrap; }
.mink-status.ok { color: var(--c-text-faint); }
.mink-status.bad { color: var(--c-warning); font-weight: 600; }
.mink-status.paused { color: var(--c-text-dim); }

/* AI 盘面解读卡片 */
.ai-card {
  margin-bottom: 6px; padding: 9px 11px;
  border: 1px solid var(--c-border); border-left: 3px solid var(--c-accent);
  border-radius: var(--radius); background: var(--c-card-bg-hover);
}
.ai-head { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
.ai-badge { font-size: var(--text-meta); font-weight: 600; color: var(--c-accent); }
.ai-note { font-size: var(--text-meta); color: var(--c-text-faint); }
.ai-cached {
  font-size: var(--text-meta); color: var(--c-text-faint);
  border: 1px solid var(--c-border); border-radius: 3px; padding: 0 4px;
}
.ai-event-badge {
  font-size: var(--text-meta); font-weight: 600; color: var(--c-warning);
  border: 1px solid var(--c-warning); border-radius: 3px; padding: 0 4px;
}
.ai-event-line {
  margin: -1px 0 6px; font-size: var(--text-meta); line-height: 1.5;
  color: var(--c-text-dim); overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.ai-loading { font-size: var(--text-meta); color: var(--c-text-dim); padding: 3px 0; }
.ai-error { font-size: var(--text-meta); color: var(--c-error); padding: 3px 0; }
.ai-stance-row { display: flex; align-items: flex-start; gap: 8px; }
.ai-stance {
  flex-shrink: 0; font-size: 12px; font-weight: 600;
  padding: 1px 6px; border-radius: 3px;
}
.ai-stance.up { color: var(--c-up); background: color-mix(in srgb, var(--c-up) 12%, transparent); }
.ai-stance.down { color: var(--c-down); background: color-mix(in srgb, var(--c-down) 12%, transparent); }
.ai-stance.flat { color: var(--c-text-dim); background: var(--c-card-bg); }
.ai-summary { font-size: 13px; line-height: 1.55; color: var(--c-text); }
.ai-points {
  margin: 4px 0 0; padding-left: 18px;
  font-size: var(--text-meta); line-height: 1.65; color: var(--c-text-dim);
}
.ai-points.risk li { color: var(--c-warning); }
.ai-sub { margin-top: 6px; font-size: var(--text-meta); font-weight: 600; color: var(--c-text-dim); }
.ai-foot { margin-top: 6px; font-size: var(--text-meta); color: var(--c-text-faint); }

/* 多栏分时全屏对比 */
.fs-overlay {
  position: fixed; inset: 0; z-index: 1500;   /* 低于 n-modal(2000)，高于页面；量分布/回放弹窗才能盖在全屏上 */
  display: flex; flex-direction: column;
  background: var(--c-bg); color: var(--c-text);
  padding: 10px 14px;
}
.fs-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 8px;
}
.fs-title { font-size: 16px; font-weight: 600; }
.fs-count { font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-hint { font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-hint b { color: var(--c-text); }
.fs-panels { flex: 1; display: grid; gap: 8px; min-height: 0; }
.fs-panel {
  display: flex; flex-direction: column; min-width: 0; min-height: 0;
  position: relative; overflow: hidden;
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-card-bg);
}
.fs-panel-head {
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
  padding: 8px 10px; border-bottom: 1px solid var(--c-border-light);
}
.fs-panel-name { font-size: 14px; font-weight: 600; }
.fs-panel-code { font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-panel-price {
  margin-left: auto; font-size: 16px; font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.fs-panel-chg { font-size: var(--text-meta); font-weight: 500; margin-left: 3px; }
.fs-panel-loading { font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-remove { margin-left: 4px; }
.fs-panel-tools {
  display: flex; align-items: center; gap: 2px; flex-wrap: wrap;
  margin-left: auto;
}
.fs-panel-tools .n-button { font-size: var(--text-meta) !important; }
.fs-quote {
  padding: 4px 10px 5px; border-bottom: 1px solid var(--c-border-light);
  font-size: var(--text-meta); color: var(--c-text-dim);
  display: flex; flex-direction: column; gap: 3px;
}
.fs-q-row { display: flex; align-items: center; gap: 4px 10px; flex-wrap: wrap; }
.fs-q-status { color: var(--c-text); font-weight: 500; }
.fs-q-time { color: var(--c-text-faint); font-variant-numeric: tabular-nums; }
.fs-q-stat b { color: var(--c-text); font-weight: 600; font-variant-numeric: tabular-nums; margin-left: 2px; }
.fs-levels { margin: 2px 8px 4px; }
.fs-panel-chart { flex: 1; min-height: 120px; }
/* 盘中相对强弱：全屏与详情页同图同口径 */
.fs-rs { margin: 0 8px 4px; padding-top: 4px; border-top: 1px dashed var(--c-border-light); }
.fs-rs-head { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; margin-bottom: 2px; }
.fs-rs-title { font-size: var(--text-meta); font-weight: 600; color: var(--c-text); }
.fs-rs-sub { font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-rs-chart { width: 100%; height: 112px; }
/* AI 卡与详情页一致：在分时图上方、参与流式布局；提醒列表仍用覆盖层避免挤压图表 */
.fs-ai {
  flex: none; margin: 2px 8px 6px; padding: 10px; max-height: 34%; overflow: auto;
  background: var(--c-card-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius); box-shadow: 0 4px 14px var(--c-shadow-color);
}
.fs-alerts {
  position: absolute; left: 8px; right: 8px; top: 96px; bottom: 8px; z-index: 12;
  background: var(--c-card-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius); box-shadow: 0 8px 24px var(--c-shadow-color);
  padding: 10px; overflow: auto;
}
.fs-ai-head, .fs-alerts-head {
  display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px;
  font-size: var(--text-meta);
}
.fs-ai-head span { color: var(--c-text-faint); }
.fs-ai-loading { font-size: var(--text-meta); color: var(--c-text-dim); }
.fs-ai-error { font-size: var(--text-meta); color: var(--c-error); }
.fs-ai-summary { font-size: 12px; line-height: 1.5; color: var(--c-text); }
.fs-ai-summary b { color: var(--c-accent); margin-right: 4px; }
.fs-ai-list { margin: 4px 0 0; padding-left: 16px; font-size: var(--text-meta); line-height: 1.55; color: var(--c-text-dim); }
.fs-ai-list.risk li { color: var(--c-warning); }
.fs-ai-sub { margin-top: 6px; font-size: var(--text-meta); font-weight: 600; color: var(--c-text-dim); }
.fs-ai-foot { margin-top: 6px; font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-alert-item { padding: 5px 0; border-top: 1px solid var(--c-border-light); }
.fs-alert-item:first-of-type { border-top: 0; }
.fs-alert-title { font-size: var(--text-meta); color: var(--c-text); font-weight: 500; }
.fs-alert-body { margin-top: 1px; font-size: var(--text-meta); line-height: 1.45; color: var(--c-text-dim); }
.fs-alert-time { margin-top: 1px; font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-alert-empty { font-size: var(--text-meta); color: var(--c-text-faint); }
.fs-panel-empty {
  position: absolute; left: 0; right: 0; top: 52%;
  text-align: center; font-size: var(--text-meta); color: var(--c-text-faint);
}
.fs-side {
  /* 从图表区开始，避免挡住面板头部的 AI/量分布/移除等按钮 */
  position: absolute; top: 96px; bottom: 0; width: 54px; z-index: 10;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer;
}
.fs-side-left { left: 0; }
.fs-side-right { right: 0; }
.fs-plus {
  width: 38px; height: 38px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 24px; line-height: 1; color: var(--c-text);
  background: var(--c-card-bg); border: 1px solid var(--c-border);
  box-shadow: 0 2px 10px var(--c-shadow-color);
  opacity: 0; transform: scale(.9);
  transition: opacity .15s, transform .15s;
}
.fs-side:hover .fs-plus { opacity: 1; transform: scale(1); }
.fs-search {
  position: absolute; top: 66px; z-index: 20; width: 272px;
  padding: 10px; display: flex; flex-direction: column; gap: 6px;
  background: var(--c-card-bg); border: 1px solid var(--c-border);
  border-radius: var(--radius); box-shadow: 0 8px 24px var(--c-shadow-color);
}
.fs-search-left { left: 56px; }
.fs-search-right { right: 56px; }
.fs-search-title { font-size: var(--text-meta); font-weight: 600; }
.fs-search-hint { font-size: var(--text-meta); color: var(--c-text-faint); }
@media (max-width: 900px) {
  .fs-panels {
    grid-template-columns: 1fr !important;
    grid-auto-rows: minmax(420px, auto);
    overflow-y: auto;
  }
  .fs-side { display: none; }
}
</style>
