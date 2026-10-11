<script setup lang="ts">
// 一个不存在的地址不该是一张白纸。此前只有 /market 一条重定向，别的错地址会落到
// 空的 RouterView 上：页面什么都不说，读者只能猜是「还在加载」「服务出错了」还是
// 「这个链接本来就是错的」（ADR-216）。这里只做一件事：说清这个地址没有对应的页面，
// 并给出回去的路。它不请求任何接口——没有数据要读，也就没有加载态可以卡住。
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const requested = computed(() => route.fullPath)
</script>

<template>
  <div class="card">
    <h3>这个地址没有对应的页面</h3>
    <p class="muted">
      你打开的 <code>{{ requested }}</code> 不在这个应用的路由表里：可能是链接抄错了一段，也可能是这一版删掉过的地址。
      这不是「还在加载」，也不是服务端出错——服务端没有收到过这个地址的请求。
    </p>
    <p class="muted">要么从左边那一列里挑一个页面，要么从这里回去。</p>
    <div class="row">
      <button @click="router.push('/')">回到研究首页</button>
      <button class="ghost" @click="router.push('/research')">去「研究策略」</button>
      <button class="ghost" @click="router.push('/lab')">去「AI 研究实验室」</button>
    </div>
    <p class="muted" style="margin-top: 10px">
      如果你是从页面上的一个按钮跳到这里的，那它是坏的：请把上面这个地址记下来。
    </p>
  </div>
</template>
