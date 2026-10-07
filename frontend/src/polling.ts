import { getCurrentScope, onScopeDispose, ref, type Ref } from 'vue'

/**
 * A poller for work the server does asynchronously.
 *
 * 回测与研究都可以在服务端跑很久，所以界面的状态只能靠问出来。这里刻意做成一个
 * 小组件而不是直接塞进某个视图：状态机（运行中 / 成功 / 失败 / 问不到）是同一套，
 * 视图只需要回答三个问题——怎么问、什么算结束、拿到之后怎么显示。
 *
 * 两条硬规则：
 * - 连续失败不等于失败。网络抖动三次才会停，并且会明确告诉视图「我停了，因为问不到」，
 *   而不是把「问不到」画成「运行失败」（ADR-088：一个模块失败不能让整页空白）。
 * - 停止之后回来的响应一律丢弃，否则一次已经结束的运行会被上一次的迟到响应覆盖。
 */
export interface PollerOptions<T> {
  /** How often to ask, in milliseconds. */
  intervalMs?: number
  /** Consecutive failures tolerated before the poller gives up. */
  failureLimit?: number
  /** One round trip. */
  fetch: () => Promise<T>
  /** Return true when the value is terminal and polling should stop. */
  isDone: (value: T) => boolean
  onUpdate?: (value: T) => void
  onError?: (error: unknown) => void
  /**
   * Called once when polling stops. `null` means "stopped because we kept failing to
   * ask", which the view must show differently from a failed run.
   */
  onSettled?: (last: T | null) => void
}

export interface Poller {
  start: () => void
  stop: () => void
  readonly active: Ref<boolean>
  /** Consecutive failed round trips; reset by any success. */
  readonly failures: Ref<number>
}

export function createPoller<T>(options: PollerOptions<T>): Poller {
  const intervalMs = options.intervalMs ?? 2000
  const failureLimit = options.failureLimit ?? 3
  const active = ref(false)
  const failures = ref(0)

  let timer: ReturnType<typeof setTimeout> | null = null
  // Every stop invalidates the generation, so a response that arrives after the stop
  // is dropped instead of resurrecting the run it belongs to.
  let generation = 0

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function stop() {
    generation += 1
    clearTimer()
    active.value = false
    failures.value = 0
  }

  async function tick(myGeneration: number) {
    if (myGeneration !== generation) return
    let terminalValue: T | null = null
    let exhausted = false
    try {
      const value = await options.fetch()
      if (myGeneration !== generation) return
      failures.value = 0
      options.onUpdate?.(value)
      if (options.isDone(value)) terminalValue = value
    } catch (error) {
      if (myGeneration !== generation) return
      failures.value += 1
      options.onError?.(error)
      if (failures.value >= failureLimit) exhausted = true
    }
    if (myGeneration !== generation) return
    if (terminalValue !== null || exhausted) {
      stop()
      options.onSettled?.(terminalValue)
      return
    }
    timer = setTimeout(() => void tick(myGeneration), intervalMs)
  }

  function start() {
    stop()
    active.value = true
    const myGeneration = generation
    void tick(myGeneration)
  }

  if (getCurrentScope()) onScopeDispose(stop)

  return { start, stop, active, failures }
}
