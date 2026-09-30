import { useEffect, useRef, useState } from 'react'

/**
 * Pixel width of an element, tracked with a ResizeObserver. Hand-built SVG
 * charts need real pixel sizes (the DepthAxis sibling has to line up with the
 * plot area), so a viewBox-only "responsive" chart is not enough here.
 */
export function useElementWidth<T extends HTMLElement>(fallback = 720) {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(fallback)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    setWidth(element.clientWidth || fallback)
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0
      if (next > 0) setWidth(next)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [fallback])

  return { ref, width }
}
