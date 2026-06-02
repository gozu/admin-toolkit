// Tetris game — ported from tetris.html by Felix Lee (https://x.com/felixleezd)
// Stripped of Supabase, leaderboard, home/score screens, external fonts.
// Shows real loading progress percentage from parent.

import { useRef, useState, useEffect, useCallback } from 'react';

// ---- constants ----
const COLS = 10;
const ROWS = 20;
const DEFAULT_CELL = 20; // px per cell
const SHAPES: Record<string, { blocks: [number, number][]; color: string }> = {
  I: { blocks: [[0, 0], [1, 0], [2, 0], [3, 0]], color: 'I' },
  O: { blocks: [[0, 0], [1, 0], [0, 1], [1, 1]], color: 'O' },
  T: { blocks: [[0, 0], [1, 0], [2, 0], [1, 1]], color: 'T' },
  S: { blocks: [[1, 0], [2, 0], [0, 1], [1, 1]], color: 'S' },
  Z: { blocks: [[0, 0], [1, 0], [1, 1], [2, 1]], color: 'Z' },
  J: { blocks: [[0, 0], [0, 1], [1, 1], [2, 1]], color: 'J' },
  L: { blocks: [[2, 0], [0, 1], [1, 1], [2, 1]], color: 'L' },
};
const SHAPE_KEYS = Object.keys(SHAPES);
const LINE_SCORES = [0, 100, 300, 500, 800];
const SPEED_BASE = 800;
const SPEED_MIN = 40;
const SPEED_DECREASE = 30;
const HARD_DROP_LOCK_DELAY_MS = 500;
const SOFT_DROP_INTERVAL_MS = 40;

// ---- color map (resolves CSS vars at paint time) ----
const PIECE_COLORS: Record<string, string> = {
  I: 'var(--neon-cyan)',
  O: 'var(--neon-amber)',
  T: 'var(--neon-purple)',
  S: 'var(--neon-green)',
  Z: 'var(--neon-red)',
  J: '#3b82f6',
  L: '#f97316',
};
const GHOST_COLOR = 'rgba(0, 245, 255, 0.1)';

interface Piece {
  blocks: [number, number][];
  color: string;
  x: number;
  y: number;
}

interface GameState {
  board: (string | null)[][];
  currentPiece: Piece;
  nextPiece: Piece;
  score: number;
  level: number;
  lines: number;
  gameActive: boolean;
  paused: boolean;
}

interface RotationAnim {
  oldBlocks: [number, number][]; // block coords BEFORE rotation
  pieceX: number;                // board X at rotation time
  pieceY: number;                // board Y at rotation time
  centerX: number;               // pixel centroid X (rotation pivot)
  centerY: number;               // pixel centroid Y
  startTime: number;             // performance.now()
  duration: number;              // 120ms
  color: string;                 // piece color key
}

// ---- helpers ----
function randomPiece(): Piece {
  const key = SHAPE_KEYS[Math.floor(Math.random() * SHAPE_KEYS.length)];
  const shape = SHAPES[key];
  return {
    blocks: shape.blocks.map(([x, y]) => [x, y] as [number, number]),
    color: shape.color,
    x: Math.floor(COLS / 2) - 1,
    y: 0,
  };
}

function absBlocks(blocks: [number, number][], ox: number, oy: number) {
  return blocks.map(([x, y]) => [x + ox, y + oy] as [number, number]);
}

function isValid(
  blocks: [number, number][],
  ox: number,
  oy: number,
  board: (string | null)[][],
) {
  return absBlocks(blocks, ox, oy).every(
    ([x, y]) => x >= 0 && x < COLS && y >= 0 && y < ROWS && !board[y][x],
  );
}

function resolveColor(css: string, ctx: CanvasRenderingContext2D): string {
  const m = css.match(/^var\((.+)\)$/);
  if (!m) return css;
  return getComputedStyle(ctx.canvas).getPropertyValue(m[1]).trim() || css;
}

function blocksCentroid(
  blocks: [number, number][],
  ox: number,
  oy: number,
  cellSize: number,
): [number, number] {
  let cx = 0, cy = 0;
  for (const [x, y] of blocks) {
    cx += (x + ox + 0.5) * cellSize;
    cy += (y + oy + 0.5) * cellSize;
  }
  return [cx / blocks.length, cy / blocks.length];
}

function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

// ---- component ----
export function TetrisGame({ cellSize = DEFAULT_CELL, progressPct = 0 }: { cellSize?: number; progressPct?: number } = {}) {
  const CELL = cellSize;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nextCanvasRef = useRef<HTMLCanvasElement>(null);
  const gs = useRef<GameState | null>(null);
  const gameLoopRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cellRef = useRef(CELL);
  cellRef.current = CELL;

  const [score, setScore] = useState(0);
  const [level, setLevel] = useState(1);
  const [lines, setLines] = useState(0);
  const [paused, setPaused] = useState(false);
  const [gameOver, setGameOver] = useState(false);
  const displayPct = Math.max(0, Math.min(100, Math.round(progressPct)));
  const [isMobile, setIsMobile] = useState(false);

  // ---- resolve resolved colors once on mount ----
  const resolvedColors = useRef<Record<string, string>>({});

  // ---- rotation animation state ----
  const rotAnimRef = useRef<RotationAnim | null>(null);
  const rafIdRef = useRef<number | null>(null);
  const hardDropLockUntilRef = useRef<number | null>(null);
  const hardDropLockTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const softDropIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- canvas paint ----
  const paint = useCallback(() => {
    const g = gs.current;
    const canvas = canvasRef.current;
    if (!g || !canvas) return;
    const C = cellRef.current;
    const ctx = canvas.getContext('2d')!;
    const w = COLS * C;
    const h = ROWS * C;
    canvas.width = w;
    canvas.height = h;

    // resolve CSS var colors once per paint (cheap, handles theme changes)
    for (const [k, v] of Object.entries(PIECE_COLORS)) {
      resolvedColors.current[k] = resolveColor(v, ctx);
    }
    const bgElevated = resolveColor('var(--bg-elevated)', ctx);

    // background
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    ctx.fillRect(0, 0, w, h);

    // empty cells
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        ctx.fillStyle = bgElevated;
        ctx.fillRect(c * C + 1, r * C + 1, C - 2, C - 2);
      }
    }

    // locked board cells
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const val = g.board[r][c];
        if (val) {
          ctx.fillStyle = resolvedColors.current[val] || '#fff';
          ctx.fillRect(c * C + 1, r * C + 1, C - 2, C - 2);
        }
      }
    }

    // ghost piece
    let ghostY = g.currentPiece.y;
    while (isValid(g.currentPiece.blocks, g.currentPiece.x, ghostY + 1, g.board)) ghostY++;
    if (ghostY !== g.currentPiece.y) {
      ctx.fillStyle = GHOST_COLOR;
      for (const [x, y] of absBlocks(g.currentPiece.blocks, g.currentPiece.x, ghostY)) {
        ctx.fillRect(x * C + 1, y * C + 1, C - 2, C - 2);
      }
    }

    // active piece
    const anim = rotAnimRef.current;
    if (anim) {
      const elapsed = performance.now() - anim.startTime;
      const t = Math.min(1, elapsed / anim.duration);
      const angle = easeOutCubic(t) * (Math.PI / 2);
      const pieceColor = resolvedColors.current[anim.color] || '#fff';

      // draw 5 trail ghosts at earlier angles with decreasing opacity
      const trailCount = 5;
      const trailOpacities = [0.08, 0.11, 0.14, 0.18, 0.23];
      for (let i = 0; i < trailCount; i++) {
        const trailT = Math.max(0, t - (trailCount - i) * 0.06);
        const trailAngle = easeOutCubic(trailT) * (Math.PI / 2);
        ctx.save();
        ctx.globalAlpha = trailOpacities[i];
        ctx.translate(anim.centerX, anim.centerY);
        ctx.rotate(trailAngle);
        ctx.translate(-anim.centerX, -anim.centerY);
        ctx.fillStyle = pieceColor;
        for (const [bx, by] of anim.oldBlocks) {
          const px = (bx + anim.pieceX) * C + 1;
          const py = (by + anim.pieceY) * C + 1;
          if (by + anim.pieceY >= 0) ctx.fillRect(px, py, C - 2, C - 2);
        }
        ctx.restore();
      }

      // draw main piece at current interpolated angle
      ctx.save();
      ctx.globalAlpha = 1;
      ctx.translate(anim.centerX, anim.centerY);
      ctx.rotate(angle);
      ctx.translate(-anim.centerX, -anim.centerY);
      ctx.fillStyle = pieceColor;
      for (const [bx, by] of anim.oldBlocks) {
        const px = (bx + anim.pieceX) * C + 1;
        const py = (by + anim.pieceY) * C + 1;
        if (by + anim.pieceY >= 0) ctx.fillRect(px, py, C - 2, C - 2);
      }
      ctx.restore();
    } else {
      ctx.fillStyle = resolvedColors.current[g.currentPiece.color] || '#fff';
      for (const [x, y] of absBlocks(g.currentPiece.blocks, g.currentPiece.x, g.currentPiece.y)) {
        if (y >= 0) ctx.fillRect(x * C + 1, y * C + 1, C - 2, C - 2);
      }
    }
  }, []);

  const paintNext = useCallback(() => {
    const g = gs.current;
    const canvas = nextCanvasRef.current;
    if (!g || !canvas) return;
    const C = cellRef.current;
    const ctx = canvas.getContext('2d')!;
    const size = 4 * C;
    canvas.width = size;
    canvas.height = size;

    const bgElevated = resolveColor('var(--bg-elevated)', ctx);
    ctx.fillStyle = bgElevated;
    ctx.fillRect(0, 0, size, size);

    ctx.fillStyle = resolvedColors.current[g.nextPiece.color] || '#fff';
    for (const [x, y] of g.nextPiece.blocks) {
      ctx.fillRect(x * C + 1, y * C + 1, C - 2, C - 2);
    }
  }, []);

  // ---- sync display state from mutable ref ----
  const syncDisplay = useCallback(() => {
    const g = gs.current;
    if (!g) return;
    setScore(g.score);
    setLevel(g.level);
    setLines(g.lines);
  }, []);

  // ---- game loop ----
  const startLoop = useCallback(() => {
    if (gameLoopRef.current) clearInterval(gameLoopRef.current);
    const g = gs.current;
    if (!g) return;
    const speed = Math.max(SPEED_MIN, SPEED_BASE - (g.level - 1) * SPEED_DECREASE);
    gameLoopRef.current = setInterval(() => {
      if (!g.gameActive || g.paused) return;
      moveDown();
    }, speed);
  }, []);

  // ---- piece logic ----
  function isHardDropGraceActive(): boolean {
    const until = hardDropLockUntilRef.current;
    return until !== null && performance.now() < until;
  }

  function clearHardDropLockState() {
    hardDropLockUntilRef.current = null;
    if (hardDropLockTimerRef.current) {
      clearTimeout(hardDropLockTimerRef.current);
      hardDropLockTimerRef.current = null;
    }
  }

  function finalizeHardDropLock() {
    hardDropLockTimerRef.current = null;
    const g = gs.current;
    if (!g || !g.gameActive || hardDropLockUntilRef.current === null) {
      clearHardDropLockState();
      return;
    }
    if (g.paused) {
      hardDropLockTimerRef.current = setTimeout(finalizeHardDropLock, 50);
      return;
    }
    if (isHardDropGraceActive()) {
      const remaining = Math.max(1, Math.ceil((hardDropLockUntilRef.current ?? performance.now()) - performance.now()));
      hardDropLockTimerRef.current = setTimeout(finalizeHardDropLock, remaining);
      return;
    }
    lockPiece();
  }

  function scheduleHardDropLock() {
    clearHardDropLockState();
    hardDropLockUntilRef.current = performance.now() + HARD_DROP_LOCK_DELAY_MS;
    hardDropLockTimerRef.current = setTimeout(finalizeHardDropLock, HARD_DROP_LOCK_DELAY_MS);
  }

  function settleCurrentPieceToFloor(scorePerCell = 0): number {
    const g = gs.current;
    if (!g) return 0;
    let dropped = 0;
    while (isValid(g.currentPiece.blocks, g.currentPiece.x, g.currentPiece.y + 1, g.board)) {
      g.currentPiece.y++;
      dropped++;
      if (scorePerCell > 0) {
        g.score += scorePerCell;
      }
    }
    return dropped;
  }

  function moveDown(): boolean {
    const g = gs.current;
    if (!g) return false;

    if (hardDropLockUntilRef.current !== null) {
      if (isHardDropGraceActive()) {
        // Grace period after hard drop: allow horizontal drag before lock.
        settleCurrentPieceToFloor(0);
        paint();
        return false;
      }
      clearHardDropLockState();
      lockPiece();
      return false;
    }

    if (isValid(g.currentPiece.blocks, g.currentPiece.x, g.currentPiece.y + 1, g.board)) {
      g.currentPiece.y++;
      paint();
      return true;
    } else {
      lockPiece();
      return false;
    }
  }

  function clearSoftDrop() {
    if (softDropIntervalRef.current) {
      clearInterval(softDropIntervalRef.current);
      softDropIntervalRef.current = null;
    }
  }

  function softDropStep() {
    const g = gs.current;
    if (!g || !g.gameActive || g.paused || isHardDropGraceActive()) {
      clearSoftDrop();
      return;
    }
    if (moveDown()) {
      g.score += 1;
      syncDisplay();
    } else {
      clearSoftDrop();
    }
  }

  function startSoftDrop() {
    if (softDropIntervalRef.current) return;
    softDropStep(); // immediate first step
    softDropIntervalRef.current = setInterval(softDropStep, SOFT_DROP_INTERVAL_MS);
  }

  function moveLeft() {
    const g = gs.current;
    if (!g) return;
    if (isValid(g.currentPiece.blocks, g.currentPiece.x - 1, g.currentPiece.y, g.board)) {
      g.currentPiece.x--;
      if (hardDropLockUntilRef.current !== null) {
        settleCurrentPieceToFloor(0);
      }
      paint();
    }
  }

  function moveRight() {
    const g = gs.current;
    if (!g) return;
    if (isValid(g.currentPiece.blocks, g.currentPiece.x + 1, g.currentPiece.y, g.board)) {
      g.currentPiece.x++;
      if (hardDropLockUntilRef.current !== null) {
        settleCurrentPieceToFloor(0);
      }
      paint();
    }
  }

  function animationTick() {
    const anim = rotAnimRef.current;
    if (!anim) { rafIdRef.current = null; return; }
    const elapsed = performance.now() - anim.startTime;
    if (elapsed >= anim.duration) {
      rotAnimRef.current = null;
      rafIdRef.current = null;
      paint();
      return;
    }
    paint();
    rafIdRef.current = requestAnimationFrame(animationTick);
  }

  function rotatePiece() {
    const g = gs.current;
    if (!g) return;
    const C = cellRef.current;
    const oldBlocks = g.currentPiece.blocks.map(([x, y]) => [x, y] as [number, number]);
    const oldX = g.currentPiece.x;
    const oldY = g.currentPiece.y;

    const rotated = g.currentPiece.blocks.map(
      ([x, y]) => [-y, x] as [number, number],
    );
    const minX = Math.min(...rotated.map(([x]) => x));
    const minY = Math.min(...rotated.map(([, y]) => y));
    const normalized = rotated.map(
      ([x, y]) => [x - minX, y - minY] as [number, number],
    );
    for (const offset of [0, -1, 1, -2, 2]) {
      if (isValid(normalized, g.currentPiece.x + offset, g.currentPiece.y, g.board)) {
        g.currentPiece.blocks = normalized;
        g.currentPiece.x += offset;

        // skip animation for O-piece (rotationally symmetric)
        if (g.currentPiece.color !== 'O') {
          const [cx, cy] = blocksCentroid(oldBlocks, oldX, oldY, C);
          rotAnimRef.current = {
            oldBlocks,
            pieceX: oldX,
            pieceY: oldY,
            centerX: cx,
            centerY: cy,
            startTime: performance.now(),
            duration: 120,
            color: g.currentPiece.color,
          };
          if (rafIdRef.current == null) {
            rafIdRef.current = requestAnimationFrame(animationTick);
          }
        } else {
          paint();
        }
        return;
      }
    }
  }

  function hardDrop() {
    const g = gs.current;
    if (!g) return;
    if (hardDropLockUntilRef.current !== null && isHardDropGraceActive()) return;
    clearSoftDrop();
    rotAnimRef.current = null;
    settleCurrentPieceToFloor(2);
    syncDisplay();
    scheduleHardDropLock();
    paint();
  }

  function lockPiece() {
    const g = gs.current;
    if (!g) return;
    clearHardDropLockState();
    rotAnimRef.current = null;
    for (const [x, y] of absBlocks(g.currentPiece.blocks, g.currentPiece.x, g.currentPiece.y)) {
      if (y >= 0 && y < ROWS) g.board[y][x] = g.currentPiece.color;
    }
    checkLines();
  }

  function checkLines() {
    const g = gs.current;
    if (!g) return;
    const fullRows: number[] = [];
    for (let r = 0; r < ROWS; r++) {
      if (g.board[r].every((c) => c !== null)) fullRows.push(r);
    }
    if (fullRows.length > 0) {
      g.score += LINE_SCORES[fullRows.length] * g.level;
      g.lines += fullRows.length;
      g.level = Math.floor(g.lines / 10) + 1;
      startLoop();
      const fullSet = new Set(fullRows);
      const remainingRows = g.board.filter((_, idx) => !fullSet.has(idx));
      const emptyRows = Array.from({ length: fullRows.length }, () => Array(COLS).fill(null));
      g.board = [...emptyRows, ...remainingRows];
    }
    syncDisplay();
    spawnPiece();
  }

  function spawnPiece() {
    const g = gs.current;
    if (!g) return;
    clearHardDropLockState();
    g.currentPiece = g.nextPiece;
    g.nextPiece = randomPiece();
    paintNext();
    if (!isValid(g.currentPiece.blocks, g.currentPiece.x, g.currentPiece.y, g.board)) {
      g.gameActive = false;
      if (gameLoopRef.current) clearInterval(gameLoopRef.current);
      setGameOver(true);
    } else {
      paint();
    }
  }

  function togglePause() {
    const g = gs.current;
    if (!g || !g.gameActive) return;
    g.paused = !g.paused;
    if (g.paused) rotAnimRef.current = null;
    setPaused(g.paused);
  }

  function restartGame() {
    const g = gs.current;
    if (!g) return;
    clearHardDropLockState();
    clearSoftDrop();
    g.board = Array.from({ length: ROWS }, () => Array(COLS).fill(null));
    g.score = 0;
    g.level = 1;
    g.lines = 0;
    g.gameActive = true;
    g.paused = false;
    g.nextPiece = randomPiece();
    g.currentPiece = randomPiece();
    setGameOver(false);
    setPaused(false);
    syncDisplay();
    paintNext();
    paint();
    startLoop();
  }

  // ---- mount: init game + countdown timer ----
  useEffect(() => {
    // detect mobile
    setIsMobile(window.matchMedia('(max-width: 600px)').matches);

    const next = randomPiece();
    const state: GameState = {
      board: Array.from({ length: ROWS }, () => Array(COLS).fill(null)),
      currentPiece: randomPiece(),
      nextPiece: next,
      score: 0,
      level: 1,
      lines: 0,
      gameActive: true,
      paused: false,
    };
    gs.current = state;

    // initial paint
    paint();
    paintNext();
    startLoop();

    // keyboard handler
    const onKey = (e: KeyboardEvent) => {
      const g = gs.current;
      if (!g || !g.gameActive) return;
      // don't intercept input fields
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      if (g.paused && e.key !== 'p' && e.key !== 'P' && e.key !== 'Escape') return;
      const hardDropGrace = isHardDropGraceActive();
      if (hardDropGrace && e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'p' && e.key !== 'P' && e.key !== 'Escape') {
        return;
      }

      switch (e.key) {
        case 'ArrowLeft':
          e.preventDefault();
          moveLeft();
          break;
        case 'ArrowRight':
          e.preventDefault();
          moveRight();
          break;
        case 'ArrowDown':
          e.preventDefault();
          if (!e.repeat) startSoftDrop();
          break;
        case 'ArrowUp':
          e.preventDefault();
          rotatePiece();
          break;
        case ' ':
          e.preventDefault();
          hardDrop();
          break;
        case 'p':
        case 'P':
        case 'Escape':
          e.preventDefault();
          togglePause();
          break;
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') clearSoftDrop();
    };

    document.addEventListener('keydown', onKey);
    document.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', clearSoftDrop);

    return () => {
      if (gameLoopRef.current) clearInterval(gameLoopRef.current);
      clearHardDropLockState();
      clearSoftDrop();
      if (rafIdRef.current != null) cancelAnimationFrame(rafIdRef.current);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', clearSoftDrop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- touch helpers ----
  const touchRepeat = useRef<{ timer: ReturnType<typeof setTimeout> | null; interval: ReturnType<typeof setInterval> | null }>({ timer: null, interval: null });
  const clearRepeat = () => {
    if (touchRepeat.current.timer) clearTimeout(touchRepeat.current.timer);
    if (touchRepeat.current.interval) clearInterval(touchRepeat.current.interval);
    touchRepeat.current = { timer: null, interval: null };
  };
  const touchAction = (action: string) => {
    const g = gs.current;
    if (!g || !g.gameActive) return;
    if (action === 'pause') { togglePause(); return; }
    if (g.paused) return;
    if (isHardDropGraceActive() && !['left', 'right'].includes(action)) return;
    const fn = () => {
      switch (action) {
        case 'left': moveLeft(); break;
        case 'right': moveRight(); break;
        case 'down': if (moveDown() && gs.current) { gs.current.score += 1; syncDisplay(); } break;
        case 'rotate': rotatePiece(); break;
        case 'drop': hardDrop(); break;
      }
    };
    fn();
    if (['left', 'right', 'down'].includes(action)) {
      clearRepeat();
      touchRepeat.current.timer = setTimeout(() => {
        touchRepeat.current.interval = setInterval(fn, 80);
      }, 200);
    }
  };

  const timerColor =
    displayPct >= 100
      ? 'var(--neon-green)'
      : displayPct >= 60
        ? 'var(--neon-cyan)'
        : 'var(--neon-amber)';

  return (
    <div className="flex flex-col items-center gap-2 py-3 px-4">
      {/* Big countdown timer at the top */}
      <div className="text-center">
        <div className="text-[10px] uppercase tracking-widest text-[var(--text-muted)] mb-1 font-bold">
          Loading diagnostic data
        </div>
        <div
          className="font-mono text-4xl font-bold tabular-nums"
          style={{ color: timerColor }}
        >
          {displayPct}%
        </div>
      </div>

      <div className="flex gap-4 items-start">
        {/* sidebar */}
        <div className="flex flex-col gap-2 min-w-[90px]">
          <StatBox label="Score" value={score.toLocaleString()} color="var(--neon-cyan)" />
          <StatBox label="Level" value={String(level)} color="var(--neon-purple)" />
          <StatBox label="Lines" value={String(lines)} color="var(--neon-green)" />
          <div className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-elevated)] p-2">
            <div className="text-[10px] uppercase tracking-widest text-[var(--text-muted)] mb-1">
              Next
            </div>
            <canvas ref={nextCanvasRef} className="mx-auto" style={{ width: 4 * CELL, height: 4 * CELL, imageRendering: 'pixelated' }} />
          </div>
        </div>

        {/* board */}
        <div className="relative">
          <div className="rounded-lg border border-[var(--border-glass)] overflow-hidden" style={{ lineHeight: 0 }}>
            <canvas
              ref={canvasRef}
              style={{
                width: COLS * CELL,
                height: ROWS * CELL,
              }}
            />
          </div>

          {/* pause overlay */}
          {paused && !gameOver && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/60 rounded-lg">
              <div className="text-center">
                <div className="text-lg font-bold text-[var(--text-primary)] mb-2">Paused</div>
                <button
                  onClick={togglePause}
                  className="px-3 py-1 text-sm rounded bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30 hover:bg-[var(--neon-cyan)]/30"
                >
                  Resume
                </button>
              </div>
            </div>
          )}

          {/* game over overlay */}
          {gameOver && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/60 rounded-lg">
              <div className="text-center">
                <div className="text-lg font-bold text-[var(--text-primary)] mb-1">Game Over</div>
                <div className="text-sm text-[var(--neon-cyan)] font-mono mb-2">
                  {score.toLocaleString()}
                </div>
                <button
                  onClick={restartGame}
                  className="px-3 py-1 text-sm rounded bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30 hover:bg-[var(--neon-cyan)]/30"
                >
                  Play Again
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* keyboard hints (desktop) / touch controls (mobile) */}
      {isMobile ? (
        <div className="flex gap-4 items-start pt-1">
          {/* d-pad */}
          <div className="grid grid-cols-3 grid-rows-2 gap-1" style={{ width: 132 }}>
            <div />
            <TouchBtn label="&#x21BB;" onAction={() => touchAction('rotate')} onEnd={clearRepeat} />
            <div />
            <TouchBtn label="&#x2190;" onAction={() => touchAction('left')} onEnd={clearRepeat} />
            <TouchBtn label="&#x2193;" onAction={() => touchAction('down')} onEnd={clearRepeat} />
            <TouchBtn label="&#x2192;" onAction={() => touchAction('right')} onEnd={clearRepeat} />
          </div>
          <div className="flex flex-col gap-1 pt-[44px]">
            <TouchBtn label="DROP" wide onAction={() => touchAction('drop')} onEnd={clearRepeat} />
            <TouchBtn label="PAUSE" wide onAction={() => touchAction('pause')} onEnd={clearRepeat} />
          </div>
        </div>
      ) : (
        <div className="flex gap-4 text-[10px] text-[var(--text-muted)]">
          <span><Kbd>&#x2190;</Kbd><Kbd>&#x2192;</Kbd> Move</span>
          <span><Kbd>&#x2191;</Kbd> Rotate</span>
          <span><Kbd>&#x2193;</Kbd> Soft</span>
          <span><Kbd>Space</Kbd> Hard</span>
          <span><Kbd>P</Kbd> Pause</span>
        </div>
      )}
    </div>
  );
}

// ---- small sub-components ----
function StatBox({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-elevated)] p-2">
      <div className="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">{label}</div>
      <div className="font-mono text-base font-bold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-block px-1 py-0.5 text-[10px] font-mono bg-[var(--bg-elevated)] border border-[var(--border-glass)] rounded text-[var(--text-secondary)] mx-0.5">
      {children}
    </kbd>
  );
}

function TouchBtn({
  label,
  wide,
  onAction,
  onEnd,
}: {
  label: string;
  wide?: boolean;
  onAction: () => void;
  onEnd: () => void;
}) {
  return (
    <button
      className={`${wide ? 'w-16 h-10 text-[10px] font-bold tracking-wide' : 'w-10 h-10 text-base'} border border-[var(--border-glass)] rounded-lg bg-[var(--bg-elevated)] text-[var(--text-secondary)] active:bg-[var(--bg-glass-hover)] active:border-[var(--neon-cyan)]/40 select-none`}
      style={{ touchAction: 'manipulation', WebkitTapHighlightColor: 'transparent' }}
      onTouchStart={(e) => { e.preventDefault(); onAction(); }}
      onTouchEnd={(e) => { e.preventDefault(); onEnd(); }}
      onTouchCancel={(e) => { e.preventDefault(); onEnd(); }}
      onMouseDown={onAction}
      onMouseUp={onEnd}
      onMouseLeave={onEnd}
      dangerouslySetInnerHTML={{ __html: label }}
    />
  );
}
