<template>
  <svg :width="Math.round(height * 66 / 26 * 10) / 10" :height="height"
       viewBox="0 0 66 26" fill="none" xmlns="http://www.w3.org/2000/svg"
       role="img" aria-label="K道">
    <defs>
      <linearGradient id="kf-up" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#f87171"/>
        <stop offset="1" stop-color="#dc2626"/>
      </linearGradient>
      <linearGradient id="kf-down" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#34d399"/>
        <stop offset="1" stop-color="#059669"/>
      </linearGradient>
    </defs>

    <!-- 三根 K 线蜡烛 -->
    <line x1="4.4" y1="8.6" x2="4.4" y2="17.6" stroke="url(#kf-down)" stroke-width="1.1" stroke-linecap="round"/>
    <rect x="2.6" y="10.8" width="3.6" height="4.6" rx="0.9" fill="url(#kf-down)"/>
    <line x1="12" y1="3.2" x2="12" y2="18" stroke="url(#kf-up)" stroke-width="1.1" stroke-linecap="round"/>
    <rect x="10.2" y="5.2" width="3.6" height="9.6" rx="0.9" fill="url(#kf-up)"/>
    <line x1="19.6" y1="7" x2="19.6" y2="16.4" stroke="url(#kf-up)" stroke-width="1.1" stroke-linecap="round"/>
    <rect x="17.8" y="9.2" width="3.6" height="4.6" rx="0.9" fill="url(#kf-up)"/>
    <!-- 买点信号 -->
    <circle cx="12.3" cy="4.4" r="1.05" fill="#ffc53d" stroke="rgba(255,255,255,.85)" stroke-width="0.6"/>

    <!-- K道 字标：Noto Sans CJK SC Bold 轮廓，绕基线 italic 斜切 8° -->
    <g transform="translate(0,21)">
      <path d="M27 1.7L30.4 1.7L31.1 -3.1L33.7 -6L37 1.7L40.7 1.7L36.1 -8.6L42.2 -15.3L38.5 -15.3L31.8 -7.9L31.7 -7.9L32.8 -15.3L29.4 -15.3" fill="url(#kf-up)"/>
      <path d="M47 -13.6C47.8 -12.7 48.8 -11.4 49.1 -10.5L51.1 -11.7C50.7 -12.6 49.7 -13.8 48.9 -14.7M54.1 -6.5L59 -6.5L58.9 -5.5L54 -5.5M53.8 -4.1L58.7 -4.1L58.5 -3.1L53.6 -3.1M54.4 -8.8L59.3 -8.8L59.2 -7.9L54.3 -7.9M52.6 -10.4L51.4 -1.6L60.5 -1.6L61.7 -10.4L57.6 -10.4L58.3 -11.4L63.2 -11.4L63.4 -13.2L60.5 -13.2L61.8 -14.8L59.8 -15.4C59.5 -14.7 58.9 -13.8 58.3 -13.2L55.5 -13.2L56.5 -13.6C56.3 -14.1 55.9 -14.9 55.5 -15.5L53.6 -14.8C53.9 -14.3 54.1 -13.7 54.3 -13.2L51.8 -13.2L51.6 -11.4L56 -11.4L55.5 -10.4M50.6 -8.9L46.4 -8.9L46.1 -6.9L48.3 -6.9L47.6 -1.9C46.7 -1.6 45.7 -0.9 44.8 -0.1L45.8 1.7C46.8 0.7 47.9 -0.4 48.5 -0.4C49 -0.4 49.5 0.1 50.3 0.5C51.5 1.2 53.1 1.4 55.3 1.4C57 1.4 60 1.3 61.2 1.2C61.3 0.6 61.7 -0.3 62 -0.9C60.3 -0.6 57.5 -0.5 55.6 -0.5C53.6 -0.5 52 -0.6 50.9 -1.2C50.3 -1.5 50 -1.8 49.6 -1.9" fill="currentColor"/>
    </g>
    <!-- 贯穿底部的趋势均线（主题自适应） -->
    <path d="M1.5 23.7 C 12 22.1, 22 25.1, 33 23.5 S 54 22.5, 64.5 23.9"
          stroke="currentColor" stroke-opacity="0.35" stroke-width="1.3" stroke-linecap="round"/>
  </svg>
</template>

<script setup>
defineProps({ height: { type: Number, default: 20 } })
</script>