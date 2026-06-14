/**
 * Tic-Tac-Toe board renderer — adapted from the LxM Match Viewer (per JJ's steer
 * to reference the LxM viewer). Canvas board with coordinate labels, last-move
 * highlight, and a win-line animation. The Ludex arena viewer feeds render(state)
 * a state produced from the bridge's readable board (state.board = 3x3 of
 * 'X'/'O'/null); the LxM applyMove/replay machinery is bypassed.
 */
class TicTacToeRenderer {
    constructor(containerElement) {
        this.container = containerElement;
        this.canvas = document.createElement('canvas');
        this.canvas.width = 480 * 2;
        this.canvas.height = 480 * 2;
        this.canvas.style.width = '100%';
        this.canvas.style.height = '100%';
        this.container.appendChild(this.canvas);
        this.ctx = this.canvas.getContext('2d');
        this.ctx.scale(2, 2);
        this._animationId = null;
    }

    render(state, turnNumber, lastMove, animate = false) {
        if (this._animationId) { cancelAnimationFrame(this._animationId); this._animationId = null; }
        const lastPos = (lastMove && lastMove.position) ? lastMove.position : null;
        this._drawBoard(state, lastPos, 1.0);
    }

    _drawBoard(state, highlightPos, newMarkOpacity) {
        const ctx = this.ctx;
        const W = 480, pad = 40, innerSize = W - pad * 2, cell = innerSize / 3;
        ctx.clearRect(0, 0, W, W);

        ctx.fillStyle = '#444466';
        ctx.font = '11px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        for (let i = 0; i < 3; i++) {
            const x = pad + i * cell + cell / 2;
            ctx.fillText(i.toString(), x, pad - 16);
            const y = pad + i * cell + cell / 2;
            ctx.fillText(i.toString(), pad - 16, y);
        }

        if (highlightPos) {
            const [hr, hc] = highlightPos;
            ctx.fillStyle = 'rgba(255, 215, 0, 0.06)';
            ctx.fillRect(pad + hc * cell, pad + hr * cell, cell, cell);
        }

        ctx.strokeStyle = '#2a2a4a';
        ctx.lineWidth = 2;
        for (let i = 1; i < 3; i++) {
            const pos = pad + i * cell;
            ctx.beginPath(); ctx.moveTo(pos, pad); ctx.lineTo(pos, W - pad); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(pad, pos); ctx.lineTo(W - pad, pos); ctx.stroke();
        }

        for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
            if (state.board[r][c]) continue;
            const cx = pad + c * cell + cell / 2, cy = pad + r * cell + cell / 2;
            ctx.fillStyle = 'rgba(42, 42, 74, 0.5)';
            ctx.beginPath(); ctx.arc(cx, cy, 3, 0, Math.PI * 2); ctx.fill();
        }

        for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
            const mark = state.board[r][c];
            if (!mark) continue;
            const cx = pad + c * cell + cell / 2, cy = pad + r * cell + cell / 2;
            const isLast = highlightPos && highlightPos[0] === r && highlightPos[1] === c;
            this._drawMark(mark, cx, cy, cell, isLast ? newMarkOpacity : 0.7, isLast);
        }
    }

    _drawMark(mark, cx, cy, cellSize, opacity, highlight = false) {
        const ctx = this.ctx;
        const size = cellSize * 0.3;
        ctx.save();
        ctx.globalAlpha = opacity;
        if (mark === 'X') {
            ctx.strokeStyle = '#00d4ff';
            ctx.lineWidth = highlight ? 5 : 3.5;
            ctx.lineCap = 'round';
            ctx.beginPath(); ctx.moveTo(cx - size, cy - size); ctx.lineTo(cx + size, cy + size); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(cx + size, cy - size); ctx.lineTo(cx - size, cy + size); ctx.stroke();
        } else {
            ctx.strokeStyle = '#ff6b35';
            ctx.lineWidth = highlight ? 5 : 3.5;
            ctx.beginPath(); ctx.arc(cx, cy, size, 0, Math.PI * 2); ctx.stroke();
        }
        ctx.restore();
    }

    renderResult(result, state) {
        const line = this._findWinLine(state.board);
        if (line) this._drawWinLine(line);
    }

    _findWinLine(board) {
        const lines = [
            [[0,0],[0,1],[0,2]], [[1,0],[1,1],[1,2]], [[2,0],[2,1],[2,2]],
            [[0,0],[1,0],[2,0]], [[0,1],[1,1],[2,1]], [[0,2],[1,2],[2,2]],
            [[0,0],[1,1],[2,2]], [[0,2],[1,1],[2,0]],
        ];
        for (const line of lines) {
            const v = line.map(([r, c]) => board[r][c]);
            if (v[0] && v[0] === v[1] && v[1] === v[2]) return line;
        }
        return null;
    }

    _drawWinLine(line) {
        const ctx = this.ctx, pad = 40, cell = (480 - pad * 2) / 3;
        const toXY = ([r, c]) => ({ x: pad + c * cell + cell / 2, y: pad + r * cell + cell / 2 });
        const s = toXY(line[0]), e = toXY(line[2]);
        ctx.save();
        ctx.strokeStyle = '#ffd700'; ctx.lineWidth = 5; ctx.lineCap = 'round';
        ctx.globalAlpha = 0.85; ctx.shadowColor = '#ffd700'; ctx.shadowBlur = 14;
        ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(e.x, e.y); ctx.stroke();
        ctx.restore();
    }
}

window.LxMRenderers = window.LxMRenderers || {};
window.LxMRenderers.tictactoe = TicTacToeRenderer;
window.LxMRenderers.tic_tac_toe = TicTacToeRenderer;   // OpenSpiel/Kaggle GA game id
