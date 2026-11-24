import { useEffect, useRef } from 'react'
import { useTheme } from '../theme'
import './GradientBackground.css'

/**
 * Rolling waves drawn by a small WebGL fragment shader (no libraries).
 * Colours come from the Onyx + Champagne palette. It is drawn at reduced resolution (the image is soft),
 * pauses when off-screen or when the tab is hidden, and with "reduce motion" draws one still frame.
 * Without WebGL the CSS gradient on the parent shows instead.
 */

const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`

const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform vec3 uC1;
uniform vec3 uC2;
uniform vec3 uC3;
uniform float uLight;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  float aspect = uRes.x / uRes.y;
  float x = uv.x * aspect;
  float t = uTime * 0.11;

  // dark ground with a faint lift toward the top
  vec3 col = mix(uC2 * mix(0.8, 0.94, uLight), uC2 * mix(1.5, 1.0, uLight), uv.y);

  // five stacked waves, back (high) to front (low); each rolls at its own pace
  for (int j = 0; j < 5; j++) {
    float i = float(j);
    float base = 0.80 - 0.15 * i;
    float w = base
      + 0.055 * sin(x * 2.1 + t * (0.8 + 0.18 * i) + i * 1.7)
      + 0.030 * sin(x * 3.9 - t * 1.1 + i * 2.9)
      + 0.014 * sin(x * 6.8 + t * 1.6 + i * 0.9);
    float d = uv.y - w;                                     // above the crest > 0
    float mask = smoothstep(0.01, -0.01, d);                // filled below the crest
    vec3 body = mix(uC2, uC1, 0.30 + 0.14 * i);
    body *= mix(0.62, 0.9, uLight) + mix(0.38, 0.1, uLight) * smoothstep(-0.45, 0.0, d);        // brighter near the crest, darker in depth
    col = mix(col, body, mask);
    col += uC3 * exp(-abs(d) * 52.0) * (0.10 + 0.04 * i) * (1.0 - 1.6 * uLight);   // soft light along the crest (a soft shade in the light theme)
  }

  float v = smoothstep(1.2, 0.2, length(uv - 0.5) * 1.2);   // darker toward the corners
  col *= mix(mix(0.75, 0.88, uLight), 1.0, v);
  col += (hash(gl_FragCoord.xy + uTime) - 0.5) * 0.02;       // film grain
  gl_FragColor = vec4(col, 1.0);
}
`

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const sh = gl.createShader(type)
  if (!sh) return null
  gl.shaderSource(sh, src)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    gl.deleteShader(sh)
    return null
  }
  return sh
}

const DARK_COLORS: [string, string, string] = ['#4a443a', '#0a0a0a', '#b3ab98']
const LIGHT_COLORS: [string, string, string] = ['#ddd2bf', '#ebe2d1', '#5d5646']

interface Props {
  /** Three palette colours: the bright ribbon, the dark ground and the highlight. */
  colors?: [string, string, string]
}

export default function GradientBackground({ colors: given }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const theme = useTheme()
  const light = theme === 'light'
  const colors = given ?? (light ? LIGHT_COLORS : DARK_COLORS)

  useEffect(() => {
    const el = canvas.current
    if (!el) return
    const gl = el.getContext('webgl', { antialias: false, alpha: false, powerPreference: 'low-power' })
    if (!gl) return

    const vs = compile(gl, gl.VERTEX_SHADER, VERT)
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG)
    const prog = gl.createProgram()
    if (!vs || !fs || !prog) return
    gl.attachShader(prog, vs)
    gl.attachShader(prog, fs)
    gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return
    gl.useProgram(prog)

    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(prog, 'aPos')
    gl.enableVertexAttribArray(loc)
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)

    const uRes = gl.getUniformLocation(prog, 'uRes')
    const uTime = gl.getUniformLocation(prog, 'uTime')
    gl.uniform3fv(gl.getUniformLocation(prog, 'uC1'), rgb(colors[0]))
    gl.uniform3fv(gl.getUniformLocation(prog, 'uC2'), rgb(colors[1]))
    gl.uniform3fv(gl.getUniformLocation(prog, 'uC3'), rgb(colors[2]))
    gl.uniform1f(gl.getUniformLocation(prog, 'uLight'), light ? 1 : 0)

    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let raf = 0
    let visible = true
    let elapsed = 0 // seconds of animation already played, kept across pauses
    let last = performance.now()

    const resize = () => {
      const scale = 0.5 // the gradient is soft: half resolution looks the same and costs a quarter
      const w = Math.max(2, Math.round(el.clientWidth * scale))
      const h = Math.max(2, Math.round(el.clientHeight * scale))
      if (el.width !== w || el.height !== h) {
        el.width = w
        el.height = h
      }
      gl.viewport(0, 0, w, h)
      gl.uniform2f(uRes, w, h)
    }

    const draw = () => {
      gl.uniform1f(uTime, elapsed + 20)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }

    const loop = (now: number) => {
      elapsed += (now - last) / 1000
      last = now
      draw()
      raf = requestAnimationFrame(loop)
    }
    const run = () => {
      cancelAnimationFrame(raf)
      if (still) {
        draw()
        return
      }
      if (visible && !document.hidden) {
        last = performance.now()
        raf = requestAnimationFrame(loop)
      }
    }

    resize()
    run()

    const ro = new ResizeObserver(() => {
      resize()
      if (still) draw()
    })
    ro.observe(el)
    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      run()
    })
    io.observe(el)
    const onVis = () => run()
    document.addEventListener('visibilitychange', onVis)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      io.disconnect()
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [colors, light])

  return <canvas ref={canvas} className="gradient-bg" aria-hidden="true" />
}
