/**
 * safety.js
 * ---------
 * Client-side logic for the Safety Monitor (Command Centre) page.
 *
 * Features:
 *  - Fetch and display full alert log from /api/safety/alerts
 *  - Render camera zone status cards
 *  - Alert acknowledgement with POST
 *  - SSE real-time alert push (shared stream with dashboard)
 *  - Auto-scroll log to newest critical alert
 *  - Highlight a specific alert when ?alert=<id> is in the URL (UC-03 deep link)
 */

"use strict";

/* ================================================================== */
/*  LIVE CAMERA FEED SIMULATION                                         */
/*  Canvas-based construction site scene with animated workers,        */
/*  AI bounding boxes, scanlines, noise, and auto zone-switching.      */
/* ================================================================== */

const CAM = (() => {
    "use strict";

    /* ══════════════════════════════════════════════════════════════
       ROLE TABLE  — hard-hat colour, vest colour, skin tones
       ══════════════════════════════════════════════════════════════ */
    const ROLES = [
        { role:"Supervisor",     hatColor:"#FFFFFF", vestColor:"#dc2626", skinTones:["#c68642","#8d5524"] },
        { role:"General Worker", hatColor:"#f59e0b", vestColor:"#ea580c", skinTones:["#b5835a","#7a4f2e"] },
        { role:"Safety Officer", hatColor:"#22c55e", vestColor:"#15803d", skinTones:["#c68642","#d4956a"] },
        { role:"Electrician",    hatColor:"#3b82f6", vestColor:"#1d4ed8", skinTones:["#b5835a","#8d5524"] },
        { role:"Engineer",       hatColor:"#ef4444", vestColor:"#991b1b", skinTones:["#c68642","#d4956a"] },
    ];

    /* ══════════════════════════════════════════════════════════════
       ZONE DEFINITIONS
       Each zone has: sky palette, floor colour, ambient glow,
       scaffolding rigs, equipment items, and worker roster.
       ══════════════════════════════════════════════════════════════ */
    const ZONES = [
        {
            id:"CAM-01", label:"ZONE A \u2013 Foundation", sector:"SECTOR 1-F",
            sky:["#060d06","#0d180d"], floor:"#192619", ambient:"rgba(74,209,100,0.11)",
            scaffolding:[{ x:0.03, y:0.12, w:0.38, floors:3 }],
            equipment:[
                { type:"excavator",      x:0.76, y:0.60 },
                { type:"concrete_blocks",x:0.14, y:0.70 },
                { type:"debris_pile",    x:0.48, y:0.74 },
            ],
            workers:[
                { x:0.28, y:0.64, vx: 0.0007, roleIdx:0, hasHat:true,  trackId:"TRK-001" },
                { x:0.52, y:0.70, vx:-0.0005, roleIdx:1, hasHat:true,  trackId:"TRK-002" },
                { x:0.67, y:0.66, vx: 0.0004, roleIdx:2, hasHat:true,  trackId:"TRK-003" },
            ],
        },
        {
            id:"CAM-02", label:"ZONE B \u2013 Material Bay", sector:"SECTOR 2-M",
            sky:["#0d0d05","#1a1a08"], floor:"#28280f", ambient:"rgba(255,152,0,0.12)",
            scaffolding:[{ x:0.46, y:0.10, w:0.52, floors:4 }],
            equipment:[
                { type:"forklift",       x:0.66, y:0.63 },
                { type:"material_stack", x:0.11, y:0.67 },
                { type:"debris_pile",    x:0.83, y:0.73 },
            ],
            workers:[
                { x:0.38, y:0.63, vx: 0.0006, roleIdx:1, hasHat:false, trackId:"TRK-004", alertLabel:"MISSING HARD HAT" },
                { x:0.61, y:0.72, vx:-0.0008, roleIdx:3, hasHat:true,  trackId:"TRK-005" },
            ],
        },
        {
            id:"CAM-03", label:"ZONE C \u2013 West Wing", sector:"SECTOR 3-W",
            sky:["#05050d","#08081a"], floor:"#181827", ambient:"rgba(33,150,243,0.10)",
            scaffolding:[
                { x:0.02, y:0.08, w:0.30, floors:5 },
                { x:0.60, y:0.10, w:0.38, floors:3 },
            ],
            equipment:[
                { type:"crane_base",     x:0.81, y:0.28 },
                { type:"concrete_blocks",x:0.20, y:0.71 },
            ],
            workers:[
                { x:0.23, y:0.65, vx: 0.0008, roleIdx:4, hasHat:true, trackId:"TRK-006" },
                { x:0.50, y:0.69, vx:-0.0006, roleIdx:1, hasHat:true, trackId:"TRK-007" },
                { x:0.72, y:0.62, vx: 0.0005, roleIdx:2, hasHat:true, trackId:"TRK-008" },
            ],
        },
        {
            id:"CAM-04", label:"ZONE D \u2013 East Wing", sector:"SECTOR 4-E",
            sky:["#0d0505","#1a0808"], floor:"#281212", ambient:"rgba(229,115,115,0.14)",
            scaffolding:[{ x:0.33, y:0.07, w:0.62, floors:4 }],
            equipment:[
                { type:"mixer",          x:0.20, y:0.66 },
                { type:"material_stack", x:0.79, y:0.70 },
                { type:"debris_pile",    x:0.54, y:0.75 },
            ],
            workers:[
                { x:0.48, y:0.61, vx: 0.0006, roleIdx:1, hasHat:false, trackId:"TRK-009", alertLabel:"MISSING HARD HAT" },
                { x:0.30, y:0.72, vx: 0.0004, roleIdx:0, hasHat:true,  trackId:"TRK-010" },
                { x:0.71, y:0.65, vx:-0.0007, roleIdx:3, hasHat:true,  trackId:"TRK-011" },
            ],
        },
    ];

    /* ── State ────────────────────────────────────────────────── */
    let _canvas, _ctx, _W, _H, _raf;
    let _zoneIdx   = 3;
    let _zone      = ZONES[_zoneIdx];
    let _tick      = 0;
    let _switchIn  = 380;
    let _fadeAlpha = 1, _fading = false;
    let _shakeX = 0, _shakeY = 0, _shakeTTL = 0;

    /* ── Noise texture ─────────────────────────────────────────── */
    let _noiseCanvas, _noiseCtx, _noiseTick = 0;
    function _buildNoiseCanvas() {
        _noiseCanvas = document.createElement("canvas");
        _noiseCanvas.width  = 256;
        _noiseCanvas.height = 128;
        _noiseCtx = _noiseCanvas.getContext("2d");
    }
    function _drawNoise() {
        if (++_noiseTick % 2 !== 0) return;
        const id = _noiseCtx.createImageData(256, 128);
        const d  = id.data;
        for (let i = 0; i < d.length; i += 4) {
            const v = Math.random() * 22 | 0;
            d[i] = d[i+1] = d[i+2] = v; d[i+3] = 30;
        }
        _noiseCtx.putImageData(id, 0, 0);
    }

    /* ── Camera shake ──────────────────────────────────────────── */
    function _triggerShake(intensity) { _shakeTTL = 14; _shakeX = (Math.random()-.5)*intensity; _shakeY = (Math.random()-.5)*intensity; }
    function _tickShake() {
        if (_shakeTTL > 0) { _shakeTTL--; _shakeX *= 0.72; _shakeY *= 0.72; }
        else { _shakeX = 0; _shakeY = 0; }
        // Subtle ambient micro-shake (camera on tripod)
        if (_tick % 7 === 0) { _shakeX += (Math.random()-.5)*0.4; _shakeY += (Math.random()-.5)*0.4; }
    }

    /* ── JPEG compression artifacts ────────────────────────────── */
    function _drawCompressionArtifacts(ctx, W, H) {
        if (Math.random() > 0.035) return;
        ctx.save();
        // Horizontal block-shift glitch line
        const by  = (Math.random() * H * 0.85) | 0;
        const bh  = (3 + Math.random() * 10) | 0;
        const bx  = (Math.random() * W * 0.3) | 0;
        const bw  = (60 + Math.random() * 160) | 0;
        try {
            const imgD = ctx.getImageData(bx, by, Math.min(bw, W-bx), bh);
            const shift = ((Math.random() * 18) - 9) | 0;
            ctx.globalAlpha = 0.55 + Math.random() * 0.3;
            ctx.putImageData(imgD, Math.max(0, bx + shift), by);
        } catch(e) {}
        // Colour channel bleed
        if (Math.random() > 0.5) {
            const cy  = (Math.random() * H * 0.8) | 0;
            ctx.globalAlpha = 0.08;
            ctx.fillStyle   = Math.random() > 0.5 ? "rgba(255,0,0,0.15)" : "rgba(0,0,255,0.12)";
            ctx.fillRect(0, cy, W, 1 + (Math.random()*2|0));
        }
        ctx.restore();
    }

    /* ══════════════════════════════════════════════════════════════
       BACKGROUND SCENE — sky, brick wall, floor with perspective grid
       ══════════════════════════════════════════════════════════════ */
    function _drawScene(ctx, W, H, zone) {
        // Sky
        const skyG = ctx.createLinearGradient(0, 0, 0, H * 0.63);
        skyG.addColorStop(0, zone.sky[0]); skyG.addColorStop(1, zone.sky[1]);
        ctx.fillStyle = skyG; ctx.fillRect(0, 0, W, H);

        // Brick wall texture
        ctx.save();
        ctx.globalAlpha = 0.065;
        ctx.strokeStyle = "#aaa"; ctx.lineWidth = 0.4;
        for (let r = 0; r < 14; r++) {
            const ry = H * 0.06 + r * 18;
            if (ry > H * 0.62) break;
            ctx.beginPath(); ctx.moveTo(0, ry); ctx.lineTo(W, ry); ctx.stroke();
            const offset = r % 2 === 0 ? 0 : 35;
            for (let cx = offset; cx < W; cx += 70) {
                ctx.beginPath(); ctx.moveTo(cx, ry); ctx.lineTo(cx, ry + 18); ctx.stroke();
            }
        }
        ctx.restore();

        // Concrete columns (structural)
        ctx.save(); ctx.globalAlpha = 0.09;
        ctx.fillStyle = "#888";
        for (let i = 1; i <= 3; i++) {
            const px = (W / 4) * i;
            ctx.fillRect(px - 7, H * 0.05, 14, H * 0.57);
            // Capital (top widening)
            ctx.fillRect(px - 14, H * 0.05, 28, 8);
        }
        ctx.restore();

        // Floor
        const floorG = ctx.createLinearGradient(0, H*0.63, 0, H);
        floorG.addColorStop(0, zone.floor); floorG.addColorStop(1, "#080808");
        ctx.fillStyle = floorG; ctx.fillRect(0, H*0.63, W, H*0.37);

        // Perspective floor grid
        ctx.save(); ctx.globalAlpha = 0.07; ctx.strokeStyle = "#ccc"; ctx.lineWidth = 0.4;
        const vp = { x: W*0.5, y: H*0.63 };
        for (let gx = 0; gx <= 10; gx++) {
            const fx = (W / 10) * gx;
            ctx.beginPath(); ctx.moveTo(vp.x + (fx - vp.x)*0.05, vp.y); ctx.lineTo(fx, H); ctx.stroke();
        }
        for (let gy = 0; gy < 7; gy++) {
            const t  = gy / 6;
            const ly = H*0.63 + t*H*0.37;
            const hw = W * (0.05 + t*0.95) / 2;
            ctx.beginPath(); ctx.moveTo(vp.x - hw, ly); ctx.lineTo(vp.x + hw, ly); ctx.stroke();
        }
        ctx.restore();

        // Horizon line
        ctx.save(); ctx.globalAlpha = 0.14; ctx.strokeStyle = "#fff"; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(0, H*0.63); ctx.lineTo(W, H*0.63); ctx.stroke();
        ctx.restore();

        // Ambient floor glow
        const amb = ctx.createRadialGradient(W*0.5, H*0.82, 0, W*0.5, H*0.82, W*0.45);
        amb.addColorStop(0, zone.ambient); amb.addColorStop(1, "transparent");
        ctx.fillStyle = amb; ctx.fillRect(0, H*0.5, W, H*0.5);
    }

    /* ══════════════════════════════════════════════════════════════
       SCAFFOLDING — multi-floor rigs with cross braces & plank detail
       ══════════════════════════════════════════════════════════════ */
    function _drawScaffolding(ctx, W, H, specs) {
        specs.forEach(s => {
            const sx    = s.x * W;
            const sw    = s.w * W;
            const baseY = H * 0.63;
            const flH   = H * 0.088;

            ctx.save();
            ctx.strokeStyle = "rgba(155,155,155,0.50)"; ctx.lineWidth = 1.4;

            // Horizontal ledgers
            for (let f = 0; f <= s.floors; f++) {
                const fy = baseY - f * flH;
                ctx.beginPath(); ctx.moveTo(sx, fy); ctx.lineTo(sx + sw, fy); ctx.stroke();
            }
            // Vertical standards
            const postCount = Math.max(2, Math.round(sw / (W * 0.07)));
            for (let p = 0; p <= postCount; p++) {
                const px = sx + (sw / postCount) * p;
                ctx.beginPath(); ctx.moveTo(px, baseY); ctx.lineTo(px, baseY - s.floors * flH); ctx.stroke();
            }
            // Diagonal cross-bracing (every other bay)
            ctx.save(); ctx.globalAlpha = 0.25; ctx.lineWidth = 0.8;
            for (let f = 0; f < s.floors; f++) {
                const fy1 = baseY - f * flH;
                const fy2 = baseY - (f+1) * flH;
                for (let p = 0; p < postCount; p += 2) {
                    const px1 = sx + (sw / postCount) * p;
                    const px2 = sx + (sw / postCount) * (p+1);
                    ctx.beginPath(); ctx.moveTo(px1, fy1); ctx.lineTo(px2, fy2); ctx.stroke();
                    ctx.beginPath(); ctx.moveTo(px2, fy1); ctx.lineTo(px1, fy2); ctx.stroke();
                }
            }
            ctx.restore();
            // Plank platforms (shaded rectangles on each ledger)
            ctx.fillStyle = "rgba(120,100,70,0.18)";
            for (let f = 1; f <= s.floors; f++) {
                const fy = baseY - f * flH;
                ctx.fillRect(sx + 2, fy - 4, sw - 4, 4);
            }
            ctx.restore();
        });
    }

    /* ══════════════════════════════════════════════════════════════
       EQUIPMENT DRAWERS
       ══════════════════════════════════════════════════════════════ */
    function _drawEquipment(ctx, W, H, items) {
        items.forEach(item => {
            ctx.save();
            const ex = item.x * W, ey = item.y * H;
            switch (item.type) {
                case "excavator":       _drawExcavator(ctx, ex, ey, W); break;
                case "forklift":        _drawForklift (ctx, ex, ey, W); break;
                case "crane_base":      _drawCrane    (ctx, ex, ey, W, H); break;
                case "mixer":           _drawMixer    (ctx, ex, ey, W); break;
                case "concrete_blocks": _drawBlocks   (ctx, ex, ey, W); break;
                case "material_stack":  _drawStack    (ctx, ex, ey, W); break;
                case "debris_pile":     _drawDebris   (ctx, ex, ey, W); break;
            }
            ctx.restore();
        });
    }

    function _drawExcavator(ctx, x, y, W) {
        const s = W * 0.042;
        // Tracks
        ctx.fillStyle = "#1f2937";
        ctx.beginPath(); ctx.roundRect(x - s*1.6, y - s*0.22, s*3.2, s*0.55, s*0.2); ctx.fill();
        ctx.fillStyle = "#374151";
        for (let i = 0; i < 5; i++) ctx.beginPath(), ctx.arc(x - s*1.2 + i*s*0.6, y + s*0.12, s*0.18, 0, Math.PI*2), ctx.fill();
        // Body
        ctx.fillStyle = "#d97706";
        ctx.beginPath(); ctx.roundRect(x - s*1.1, y - s*0.9, s*2.2, s*0.7, 3); ctx.fill();
        // Cab
        ctx.fillStyle = "#92400e";
        ctx.beginPath(); ctx.roundRect(x - s*0.5, y - s*1.7, s, s*0.85, 3); ctx.fill();
        // Cab window
        ctx.fillStyle = "rgba(147,210,255,0.3)";
        ctx.beginPath(); ctx.roundRect(x - s*0.38, y - s*1.58, s*0.76, s*0.5, 2); ctx.fill();
        // Animated arm
        const ang = -0.5 + Math.sin(_tick * 0.014) * 0.35;
        const bx2 = x + s*0.2 + Math.cos(ang)*s*2.2;
        const by2 = y - s*0.9 + Math.sin(ang)*s*2.2;
        ctx.strokeStyle = "#b45309"; ctx.lineWidth = s*0.28;
        ctx.beginPath(); ctx.moveTo(x + s*0.2, y - s*0.9); ctx.lineTo(bx2, by2); ctx.stroke();
        // Bucket
        ctx.fillStyle = "#78350f";
        ctx.beginPath(); ctx.arc(bx2, by2, s*0.32, 0, Math.PI*2); ctx.fill();
    }

    function _drawForklift(ctx, x, y, W) {
        const s = W * 0.034;
        // Body
        ctx.fillStyle = "#1d4ed8";
        ctx.beginPath(); ctx.roundRect(x - s, y - s*0.9, s*2, s*0.9, 2); ctx.fill();
        // Cab
        ctx.fillStyle = "#1e3a8a";
        ctx.beginPath(); ctx.roundRect(x - s*0.55, y - s*1.75, s*1.1, s*0.9, 3); ctx.fill();
        ctx.fillStyle = "rgba(147,210,255,0.25)";
        ctx.beginPath(); ctx.roundRect(x - s*0.42, y - s*1.62, s*0.84, s*0.5, 2); ctx.fill();
        // Wheels
        ctx.fillStyle = "#111";
        [x - s*0.7, x + s*0.7].forEach(wx => {
            ctx.beginPath(); ctx.arc(wx, y + s*0.05, s*0.38, 0, Math.PI*2); ctx.fill();
            ctx.strokeStyle = "#555"; ctx.lineWidth = s*0.1;
            ctx.beginPath(); ctx.arc(wx, y + s*0.05, s*0.22, 0, Math.PI*2); ctx.stroke();
        });
        // Mast + forks
        ctx.strokeStyle = "#93c5fd"; ctx.lineWidth = s*0.22;
        ctx.beginPath(); ctx.moveTo(x + s, y - s*1.8); ctx.lineTo(x + s, y - s*0.1); ctx.stroke();
        ctx.strokeStyle = "#9ca3af"; ctx.lineWidth = s*0.13;
        ctx.beginPath(); ctx.moveTo(x + s, y - s*0.35); ctx.lineTo(x + s*2.4, y - s*0.35); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x + s, y - s*0.15); ctx.lineTo(x + s*2.4, y - s*0.15); ctx.stroke();
    }

    function _drawCrane(ctx, x, y, W, H) {
        const s = W * 0.028;
        // Tower
        ctx.strokeStyle = "#6b7280"; ctx.lineWidth = s*0.6;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y - H*0.48); ctx.stroke();
        // Tower ladder detail
        ctx.save(); ctx.globalAlpha = 0.3; ctx.lineWidth = s*0.15;
        for (let ry = y - H*0.05; ry > y - H*0.45; ry -= H*0.04) {
            ctx.beginPath(); ctx.moveTo(x - s*0.4, ry); ctx.lineTo(x + s*0.4, ry); ctx.stroke();
        }
        ctx.restore();
        // Jib
        ctx.lineWidth = s*0.28; ctx.strokeStyle = "#9ca3af";
        ctx.beginPath(); ctx.moveTo(x - s*4, y - H*0.465); ctx.lineTo(x + s*9, y - H*0.465); ctx.stroke();
        // Stay cables
        ctx.lineWidth = 0.7;
        [[x + s*6, y - H*0.465],[x + s*2, y - H*0.465]].forEach(([cx2, cy2]) => {
            ctx.beginPath(); ctx.moveTo(x, y - H*0.48); ctx.lineTo(cx2, cy2); ctx.stroke();
        });
        // Hoist rope (animated)
        const ropeEnd = y - H*0.08 + Math.sin(_tick * 0.025) * H*0.06;
        ctx.lineWidth = 0.9;
        ctx.beginPath(); ctx.moveTo(x + s*7, y - H*0.465); ctx.lineTo(x + s*7, ropeEnd); ctx.stroke();
        // Hook
        ctx.strokeStyle = "#d1d5db"; ctx.lineWidth = s*0.2;
        ctx.beginPath(); ctx.arc(x + s*7, ropeEnd + s*0.5, s*0.5, 0, Math.PI); ctx.stroke();
        // Counterweight
        ctx.fillStyle = "#4b5563";
        ctx.beginPath(); ctx.roundRect(x - s*5.5, y - H*0.48, s*2.5, s*1.4, 2); ctx.fill();
    }

    function _drawMixer(ctx, x, y, W) {
        const s = W * 0.030;
        // Chassis
        ctx.fillStyle = "#374151";
        ctx.beginPath(); ctx.roundRect(x - s*1.2, y - s*0.3, s*2.4, s*0.5, 3); ctx.fill();
        // Wheels
        ctx.fillStyle = "#111";
        [x - s*0.85, x + s*0.85].forEach(wx => {
            ctx.beginPath(); ctx.arc(wx, y + s*0.22, s*0.34, 0, Math.PI*2); ctx.fill();
        });
        // Rotating drum
        const drumAngle = (_tick * 0.03) % (Math.PI*2);
        ctx.save(); ctx.translate(x + s*0.1, y - s*0.85); ctx.rotate(drumAngle);
        ctx.fillStyle = "#4b5563";
        ctx.beginPath(); ctx.ellipse(0, 0, s*0.95, s*0.6, 0, 0, Math.PI*2); ctx.fill();
        // Drum fins
        ctx.strokeStyle = "#6b7280"; ctx.lineWidth = s*0.12;
        for (let i = 0; i < 6; i++) {
            const a = (Math.PI*2/6)*i;
            ctx.beginPath(); ctx.moveTo(0,0); ctx.lineTo(Math.cos(a)*s*0.8, Math.sin(a)*s*0.5); ctx.stroke();
        }
        ctx.restore();
        // Chute
        ctx.fillStyle = "#374151";
        ctx.beginPath(); ctx.moveTo(x - s*0.5, y - s*0.3); ctx.lineTo(x - s*1.4, y + s*0.1); ctx.lineTo(x - s*1.0, y + s*0.1); ctx.lineTo(x - s*0.2, y - s*0.3); ctx.fill();
    }

    function _drawBlocks(ctx, x, y, W) {
        const s = W * 0.024;
        for (let row = 0; row < 4; row++) {
            for (let col = 0; col < 5; col++) {
                const bx = x + (col - 2.5) * s*1.08 + (row % 2)*s*0.54;
                const by = y - row * s*0.52;
                const shade = 0.28 + row*0.05 + (col%2)*0.04;
                ctx.fillStyle = `rgba(120,120,120,${shade})`;
                ctx.beginPath(); ctx.roundRect(bx, by - s*0.48, s, s*0.48, 1); ctx.fill();
                ctx.strokeStyle = "rgba(0,0,0,0.35)"; ctx.lineWidth = 0.5;
                ctx.strokeRect(bx, by - s*0.48, s, s*0.48);
            }
        }
    }

    function _drawStack(ctx, x, y, W) {
        const s = W * 0.028;
        for (let i = 6; i >= 0; i--) {
            ctx.fillStyle = i % 2 === 0 ? "#6b605a" : "#52483f";
            ctx.fillRect(x - s*2.2, y - i * s*0.24, s*4.4, s*0.22);
            if (i === 0) {
                // Bottom support blocks
                ctx.fillStyle = "#374151";
                ctx.fillRect(x - s*2.0, y + s*0.04, s*0.6, s*0.3);
                ctx.fillRect(x + s*1.4, y + s*0.04, s*0.6, s*0.3);
            }
        }
        // End cap (perspective)
        ctx.fillStyle = "#3d342c";
        ctx.beginPath();
        ctx.moveTo(x + s*2.2, y);
        ctx.lineTo(x + s*2.2, y - 7*s*0.24);
        ctx.lineTo(x + s*2.5, y - 7*s*0.24 - s*0.1);
        ctx.lineTo(x + s*2.5, y - s*0.1);
        ctx.closePath(); ctx.fill();
    }

    function _drawDebris(ctx, x, y, W) {
        const s = W * 0.038;
        // Main pile shape
        ctx.fillStyle = "#44403c";
        ctx.beginPath();
        ctx.moveTo(x - s*1.1, y);
        ctx.quadraticCurveTo(x - s*0.6, y - s*0.55, x - s*0.1, y - s*0.72);
        ctx.quadraticCurveTo(x + s*0.5, y - s*0.48, x + s*1.1, y);
        ctx.closePath(); ctx.fill();
        // Rubble chunks
        const chunks = [[0.6,0.55,0.08],[-0.4,0.35,0.10],[0.1,0.62,0.07],[-0.7,0.18,0.09],[0.85,0.25,0.07]];
        chunks.forEach(([cx, cy, cr]) => {
            ctx.fillStyle = `rgba(87,83,78,${0.7 + Math.random()*0.2})`;
            ctx.beginPath(); ctx.arc(x + cx*s, y - cy*s, cr*s, 0, Math.PI*2); ctx.fill();
        });
        // Rebar sticking out
        ctx.save(); ctx.strokeStyle = "#6b7280"; ctx.lineWidth = 1.2; ctx.globalAlpha = 0.6;
        ctx.beginPath(); ctx.moveTo(x - s*0.3, y - s*0.5); ctx.lineTo(x - s*0.1, y - s); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x + s*0.5, y - s*0.35); ctx.lineTo(x + s*0.8, y - s*0.82); ctx.stroke();
        ctx.restore();
    }

    /* ══════════════════════════════════════════════════════════════
       WORKER SILHOUETTE — hi-vis vest, role-coloured hard hat, skin
       ══════════════════════════════════════════════════════════════ */
    function _drawWorker(ctx, wx, wy, roleIdx, hasHat) {
        const role  = ROLES[roleIdx] || ROLES[1];
        const skin  = role.skinTones[(roleIdx + _tick) % role.skinTones.length > 0.5 ? 1 : 0];
        const W_B   = 23, H_B = 52;
        const headR = W_B * 0.25;
        const headY = wy - H_B * 0.52 + headR;

        // Ground shadow
        ctx.save(); ctx.globalAlpha = 0.22;
        ctx.fillStyle = "#000";
        ctx.beginPath(); ctx.ellipse(wx, wy + H_B*0.17, W_B*0.55, H_B*0.065, 0, 0, Math.PI*2); ctx.fill();
        ctx.restore();

        const legSwing = Math.sin(_tick * 0.09 + wx) * 4.5;
        const armSwing = -legSwing;

        // Legs
        ctx.fillStyle = "#1a2030";
        ctx.beginPath(); ctx.roundRect(wx - W_B*0.24, wy - H_B*0.22, W_B*0.2, H_B*0.39, 2); ctx.fill();
        ctx.fillStyle = "#0f1520";
        ctx.beginPath(); ctx.roundRect(wx + W_B*0.04, wy - H_B*0.22 + legSwing*0.4, W_B*0.2, H_B*0.39, 2); ctx.fill();
        // Boots
        ctx.fillStyle = "#0a0a0a";
        ctx.beginPath(); ctx.roundRect(wx - W_B*0.28, wy + H_B*0.15, W_B*0.26, W_B*0.13, 2); ctx.fill();
        ctx.beginPath(); ctx.roundRect(wx + W_B*0.02, wy + H_B*0.15 + legSwing*0.3, W_B*0.26, W_B*0.13, 2); ctx.fill();

        // Vest body
        ctx.fillStyle = role.vestColor;
        ctx.beginPath(); ctx.roundRect(wx - W_B*0.38, wy - H_B*0.44, W_B*0.76, H_B*0.42, 3); ctx.fill();
        // Reflective strips
        ctx.save(); ctx.globalAlpha = 0.45; ctx.fillStyle = "#e5e7eb";
        ctx.fillRect(wx - W_B*0.38, wy - H_B*0.3, W_B*0.76, 2.5);
        ctx.fillRect(wx - W_B*0.38, wy - H_B*0.17, W_B*0.76, 2.5);
        ctx.restore();

        // Arms
        ctx.fillStyle = role.vestColor;
        ctx.beginPath(); ctx.roundRect(wx - W_B*0.56, wy - H_B*0.4 + armSwing*0.6, W_B*0.2, H_B*0.32, 2); ctx.fill();
        ctx.beginPath(); ctx.roundRect(wx + W_B*0.36, wy - H_B*0.4 - armSwing*0.6, W_B*0.2, H_B*0.32, 2); ctx.fill();
        // Gloves
        ctx.fillStyle = "#374151";
        ctx.beginPath(); ctx.arc(wx - W_B*0.46, wy - H_B*0.1 + armSwing*0.6, W_B*0.14, 0, Math.PI*2); ctx.fill();
        ctx.beginPath(); ctx.arc(wx + W_B*0.46, wy - H_B*0.1 - armSwing*0.6, W_B*0.14, 0, Math.PI*2); ctx.fill();

        // Neck + head
        ctx.fillStyle = skin;
        ctx.fillRect(wx - W_B*0.12, headY + headR*0.6, W_B*0.24, headR*0.65);
        ctx.beginPath(); ctx.arc(wx, headY, headR, 0, Math.PI*2); ctx.fill();

        // Minimal face
        ctx.fillStyle = "rgba(0,0,0,0.3)";
        ctx.beginPath(); ctx.arc(wx - headR*0.3, headY + headR*0.1, headR*0.11, 0, Math.PI*2); ctx.fill();
        ctx.beginPath(); ctx.arc(wx + headR*0.3, headY + headR*0.1, headR*0.11, 0, Math.PI*2); ctx.fill();
        ctx.beginPath(); ctx.arc(wx, headY + headR*0.42, headR*0.22, 0, Math.PI); ctx.stroke();

        // ── Hard hat ────────────────────────────────────────────
        if (hasHat) {
            // Brim
            ctx.fillStyle = role.hatColor;
            ctx.beginPath(); ctx.ellipse(wx, headY - headR*0.05, headR*1.5, headR*0.38, 0, Math.PI, 0); ctx.fill();
            // Crown dome
            ctx.beginPath();
            ctx.arc(wx, headY - headR*0.2, headR*1.08, Math.PI, 0); ctx.fill();
            // Crown top
            ctx.beginPath(); ctx.ellipse(wx, headY - headR*1.25, headR*0.85, headR*0.28, 0, 0, Math.PI*2); ctx.fill();
            // Suspension ridge line
            ctx.save(); ctx.globalAlpha = 0.2; ctx.strokeStyle = "#000"; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(wx - headR*0.8, headY - headR*0.5); ctx.lineTo(wx + headR*0.8, headY - headR*0.5); ctx.stroke();
            ctx.restore();
            // Specular highlight
            ctx.save(); ctx.globalAlpha = 0.35; ctx.fillStyle = "#fff";
            ctx.beginPath(); ctx.ellipse(wx - headR*0.28, headY - headR*0.85, headR*0.28, headR*0.13, -0.45, 0, Math.PI*2); ctx.fill();
            ctx.restore();
        }
    }

    /* ══════════════════════════════════════════════════════════════
       AI BOUNDING BOX — corner brackets, scan line, tracking ID,
       role/alert label with confidence %, sync with alert log
       ══════════════════════════════════════════════════════════════ */
    function _drawBoundingBox(ctx, wx, wy, wkr, pulse) {
        const W_B = 28, H_B = 60;
        const x1  = wx - W_B*0.62, y1 = wy - H_B*0.64;
        const x2  = wx + W_B*0.62, y2 = wy + H_B*0.24;
        const bw  = x2 - x1, bh = y2 - y1;
        const isAlert = !wkr.hasHat;
        const col  = isAlert
            ? `rgba(229,115,115,${0.72 + pulse*0.28})`
            : `rgba(74,209,100,${0.42 + pulse*0.22})`;

        // Corner-bracket style (no full rectangle)
        ctx.save();
        ctx.shadowColor = col; ctx.shadowBlur = isAlert ? 18 : 7;
        ctx.strokeStyle = col; ctx.lineWidth   = isAlert ? 2.4 : 1.6;
        const cs = Math.min(bw, bh) * 0.23;
        ctx.beginPath();
        ctx.moveTo(x1, y1+cs); ctx.lineTo(x1,y1); ctx.lineTo(x1+cs,y1);
        ctx.moveTo(x2-cs,y1);  ctx.lineTo(x2,y1); ctx.lineTo(x2,y1+cs);
        ctx.moveTo(x2,y2-cs);  ctx.lineTo(x2,y2); ctx.lineTo(x2-cs,y2);
        ctx.moveTo(x1+cs,y2);  ctx.lineTo(x1,y2); ctx.lineTo(x1,y2-cs);
        ctx.stroke();
        ctx.restore();

        // Animated horizontal scan line inside box
        const scanY = y1 + ((_tick * 1.9) % bh + bh) % bh;
        ctx.save();
        ctx.beginPath(); ctx.rect(x1, y1, bw, bh); ctx.clip();
        const sg = ctx.createLinearGradient(0, scanY-12, 0, scanY+12);
        sg.addColorStop(0, "transparent");
        sg.addColorStop(0.5, isAlert ? "rgba(229,115,115,0.28)" : "rgba(74,209,100,0.18)");
        sg.addColorStop(1,   "transparent");
        ctx.fillStyle = sg; ctx.fillRect(x1, scanY-12, bw, 24);
        ctx.restore();

        // Tracking ID — top-right corner chip
        ctx.save();
        ctx.font = "bold 8px monospace";
        const tw  = ctx.measureText(wkr.trackId).width;
        ctx.fillStyle = "rgba(0,0,0,0.72)";
        ctx.beginPath(); ctx.roundRect(x2 - tw - 10, y1 - 1, tw + 8, 13, 2); ctx.fill();
        ctx.fillStyle = col; ctx.fillText(wkr.trackId, x2 - tw - 6, y1 + 9);
        ctx.restore();

        // Role / alert label — bottom pill
        const role = ROLES[wkr.roleIdx ?? 1];
        const lbl  = isAlert
            ? `\u26a0 ${wkr.alertLabel || "MISSING HARD HAT"} \u00b7 ${wkr._conf}%`
            : `${role.role.toUpperCase()} \u00b7 ${wkr._conf}%`;
        ctx.save();
        ctx.font = "bold 9px monospace";
        const lw  = ctx.measureText(lbl).width;
        const lx  = Math.max(2, Math.min(x1, _W - lw - 14));
        const ly  = y1 - 17;
        ctx.fillStyle = isAlert ? "rgba(185,28,28,0.88)" : "rgba(20,83,45,0.85)";
        ctx.beginPath(); ctx.roundRect(lx, ly, lw + 12, 14, 3); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.fillText(lbl, lx + 6, ly + 10);
        ctx.restore();
    }

    /* ── Scanlines ─────────────────────────────────────────────── */
    function _drawScanlines(ctx, W, H) {
        ctx.save(); ctx.globalAlpha = 0.06;
        for (let y = 0; y < H; y += 3) { ctx.fillStyle = "#000"; ctx.fillRect(0, y, W, 1); }
        ctx.restore();
    }

    /* ── Vignette ──────────────────────────────────────────────── */
    function _drawVignette(ctx, W, H) {
        const v = ctx.createRadialGradient(W/2, H/2, H*0.2, W/2, H/2, H*0.95);
        v.addColorStop(0, "transparent"); v.addColorStop(1, "rgba(0,0,0,0.72)");
        ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
    }

    /* ── Sync bounding boxes with live alert log ───────────────── */
    function _syncAlertsFromLog() {
        const logItems = document.querySelectorAll("#alertLog .log-item");
        let alertCount = 0;
        logItems.forEach(li => {
            if (li.classList.contains("critical") || li.classList.contains("medium") || li.classList.contains("warning")) alertCount++;
        });
        // Trigger a shake when new critical alerts appear
        if (alertCount > 0 && _tick % 120 === 0) _triggerShake(3);
    }

    /* ── Zone switch ───────────────────────────────────────────── */
    function _switchZone() {
        _fading = true; _fadeAlpha = 0;
        _zoneIdx = (_zoneIdx + 1) % ZONES.length;
        _zone    = ZONES[_zoneIdx];
        _switchIn = 420 + (Math.random() * 260 | 0);
        _triggerShake(5);

        const ci = document.getElementById("camInfo");
        const cb = document.getElementById("camZoneBadge");
        const ap = document.getElementById("alertPillOverlay");
        if (ci) ci.textContent = `${_zone.id} | ${_zone.label.split("\u2013")[0].trim()} | 1080p`;
        if (cb) cb.innerHTML   = `${_zone.label.split("\u2013")[0].trim()}<br>${_zone.sector}`;
        if (ap) {
            const hasAlert = _zone.workers.some(w => !w.hasHat);
            ap.style.display = hasAlert ? "inline-block" : "none";
            ap.textContent   = hasAlert ? "\u26a0 DETECTION" : "";
        }
    }

    /* ── Main render loop ──────────────────────────────────────── */
    function _render() {
        _raf = requestAnimationFrame(_render);
        _tick++;
        _W = _canvas.offsetWidth;
        _H = _canvas.offsetHeight;
        if (_canvas.width  !== _W) _canvas.width  = _W;
        if (_canvas.height !== _H) _canvas.height = _H;

        if (_switchIn <= 0) _switchZone();
        _switchIn--;

        if (_fading) { _fadeAlpha = Math.min(1, _fadeAlpha + 0.065); if (_fadeAlpha >= 1) _fading = false; }
        else { _fadeAlpha = 1; }

        _tickShake();
        if (_tick % 60 === 0) _syncAlertsFromLog();

        const ctx = _ctx;
        ctx.save();
        ctx.translate(_shakeX, _shakeY);

        _drawScene(ctx, _W, _H, _zone);
        _drawScaffolding(ctx, _W, _H, _zone.scaffolding || []);
        _drawEquipment(ctx, _W, _H, _zone.equipment  || []);

        // Noise grain
        _drawNoise();
        ctx.save(); ctx.globalAlpha = 0.32;
        ctx.fillStyle = ctx.createPattern(_noiseCanvas, "repeat");
        ctx.fillRect(0, 0, _W, _H); ctx.restore();

        // Workers + detection boxes
        ctx.save(); ctx.globalAlpha = _fadeAlpha;
        _zone.workers.forEach(wk => {
            wk.x += wk.vx;
            if (wk.x < 0.06 || wk.x > 0.94) wk.vx *= -1;
            if (!wk._conf || _tick % 50 === 0) wk._conf = 91 + (Math.random() * 8 | 0);
            const wx    = wk.x * _W;
            const wy    = wk.y * _H;
            const pulse = (Math.sin(_tick * 0.065 + (wk.trackId?.charCodeAt(7) || 0)) + 1) / 2;
            _drawWorker(ctx, wx, wy, wk.roleIdx ?? 1, wk.hasHat);
            _drawBoundingBox(ctx, wx, wy, wk, pulse);
        });
        ctx.restore();

        _drawScanlines(ctx, _W, _H);
        _drawCompressionArtifacts(ctx, _W, _H);
        _drawVignette(ctx, _W, _H);

        // Fade-to-black overlay on zone switch
        if (_fadeAlpha < 1) {
            ctx.save(); ctx.globalAlpha = 1 - _fadeAlpha;
            ctx.fillStyle = "#000"; ctx.fillRect(0, 0, _W, _H);
            ctx.restore();
        }

        ctx.restore(); // end shake transform

        // Live timestamp
        const ts = document.getElementById("camTimestamp");
        if (ts) {
            const n = new Date();
            ts.textContent = n.toLocaleTimeString("en-US",{hour12:false}) + " \u00b7 REC \u25cf";
        }
    }


    return {
        start() {
            _canvas = document.getElementById("camCanvas");
            if (!_canvas) return;
            _ctx = _canvas.getContext("2d");
            _buildNoiseCanvas();
            _switchIn = 380;

            const ci = document.getElementById("camInfo");
            const cb = document.getElementById("camZoneBadge");
            const ap = document.getElementById("alertPillOverlay");
            if (ci) ci.textContent = `${_zone.id} | ${_zone.label.split("\u2013")[0].trim()} | 1080p`;
            if (cb) cb.innerHTML   = `${_zone.label.split("\u2013")[0].trim()}<br>${_zone.sector}`;
            if (ap) {
                const hasAlert = _zone.workers.some(w => !w.hasHat);
                ap.style.display = hasAlert ? "inline-block" : "none";
                ap.textContent   = hasAlert ? "\u26a0 DETECTION" : "";
            }

            _render();
        },
        stop() { cancelAnimationFrame(_raf); },
    };
})();

/* ================================================================== */
/*  Project context — dropdown + localStorage (shared with Dashboard)   */
/* ================================================================== */

const SAFETY_USER_ID = "usr-001";

const _SP = new URLSearchParams(window.location.search);
const _HIGHLIGHT_ID = _SP.get("alert") || "";

let safetyActiveProjectId =
    (window.VeritasProjectContext?.parseFromUrl(_SP)) ||
    (window.VeritasProjectContext?.readPersisted()) ||
    "";

/** Build an API URL that always carries the current project context. */
function safetyUrl(path) {
    const sep = path.includes("?") ? "&" : "?";
    return safetyActiveProjectId
        ? `${path}${sep}project_id=${encodeURIComponent(safetyActiveProjectId)}`
        : path;
}

async function loadSafetyProjectSwitcher() {
    if (!window.VeritasProjectContext) return;
    const wrap = document.getElementById("projectSwitcher");
    if (!wrap) return;
    try {
        const projects = await VeritasProjectContext.fetchProjectsList(SAFETY_USER_ID);
        if (!projects.length) return;

        safetyActiveProjectId = VeritasProjectContext.resolveActiveId(projects, safetyActiveProjectId);
        if (!safetyActiveProjectId) return;

        window.VeritasProjectContext.writePersisted(safetyActiveProjectId);
        const url = new URL(window.location.href);
        url.searchParams.set("project_id", safetyActiveProjectId);
        window.history.replaceState({}, "", url);

        wrap.innerHTML = `
            <select id="projectSelect" onchange="switchSafetyProject(this.value)"
                style="background:var(--bg-card,#1C1C1E);color:var(--text-main,#fff);
                       border:1px solid var(--border,#333);padding:6px 12px;
                       border-radius:8px;font-size:0.85rem;cursor:pointer;min-width:220px;">
                ${projects.map(p => `
                    <option value="${p.id}" ${p.id === safetyActiveProjectId ? "selected" : ""}>
                        ${p.name} · ${p.completion}%
                    </option>
                `).join("")}
            </select>
        `;
    } catch (e) {
        console.warn("[Safety Switcher] Could not load projects:", e);
    }
}

function switchSafetyProject(projectId) {
    safetyActiveProjectId = projectId;
    VeritasProjectContext.writePersisted(projectId);
    const sel = document.getElementById("projectSelect");
    if (sel && sel.value !== projectId) sel.value = projectId;
    const url = new URL(window.location.href);
    url.searchParams.set("project_id", projectId);
    window.history.replaceState({}, "", url);
    updateSafetyNavLinks();
    loadAlertLog();
    loadZones();
    if (typeof showToast === "function") showToast("Switched project context.", "info");
}

function updateSafetyNavLinks() {
    const pid = encodeURIComponent(safetyActiveProjectId);
    const home = document.getElementById("navLinkHome");
    const recent = document.getElementById("navLinkRecentAlerts");
    const rp = document.getElementById("navLinkResourcePlan");
    const vr = document.getElementById("navLinkVrTraining");
    const rl = document.getElementById("navLinkResourciist");
    if (home) {
        home.href = safetyActiveProjectId ? `/dashboard?project=${pid}` : "/dashboard";
    }
    if (recent) {
        recent.href = safetyActiveProjectId ? `/safety?project_id=${pid}` : "/safety";
    }
    if (rp) {
        rp.href = safetyActiveProjectId ? `/resource-plan?project_id=${pid}` : "/resource-plan";
    }
    if (vr) {
        vr.href = safetyActiveProjectId ? `/vr-training?project_id=${pid}` : "/vr-training";
    }
    if (rl) {
        rl.href = safetyActiveProjectId ? `/resourciist?project_id=${pid}` : "/resourciist";
    }
}

/* ================================================================== */
/*  Alert log renderer                                                  */
/* ================================================================== */

const SEVERITY_CONFIG = {
    critical: { label: "CRITICAL", colour: "var(--accent-red)",    cls: "critical" },
    medium:   { label: "MEDIUM",   colour: "#FFC107",            cls: "medium"   },
    warning:  { label: "WARNING",  colour: "var(--accent-orange)", cls: "warning"  },
    info:     { label: "INFO",     colour: "var(--text-secondary)", cls: ""         },
};

function renderLogItem(alert) {
    const cfg        = SEVERITY_CONFIG[alert.severity] ?? SEVERITY_CONFIG.info;
    const isHighlight = alert.id === _HIGHLIGHT_ID;
    return `
    <div class="log-item ${cfg.cls}${isHighlight ? " log-highlighted" : ""}" id="log-${alert.id}">
        <div class="log-time">${alert.timestamp}</div>
        <div class="log-msg" style="color:${cfg.colour};font-weight:${alert.severity === 'critical' || alert.severity === 'medium' ? 600 : 400}">
            ${cfg.label}: ${alert.title}
        </div>
        <div class="log-detail text-muted" style="font-size:0.8rem;margin-top:3px">${alert.detail ?? ""}</div>
        <span class="log-zone">${alert.zone}</span>
        <span class="log-camera text-muted" style="font-size:0.72rem;margin-left:8px">${alert.camera ?? ""}</span>
        ${alert.confidence ? `<span class="log-conf text-muted" style="font-size:0.72rem;margin-left:6px">· ${alert.confidence}% confidence</span>` : ""}
        ${!alert.acknowledged ? `<button class="ack-btn" data-id="${alert.id}">Ack</button>` : `<span class="ack-done">✓ Acked</span>`}
    </div>`;
}

async function loadAlertLog() {
    const log = document.getElementById("alertLog");
    if (!log) return;

    try {
        const json   = await apiFetch(safetyUrl("/api/safety/alerts"));
        const alerts = json.data || [];

        const newBadge = document.getElementById("newAlertsBadge");
        const unackedN = alerts.filter(a => !a.acknowledged).length;
        if (newBadge) newBadge.textContent = `${unackedN} New`;

        if (!alerts.length) {
            log.innerHTML = `<p style="color:var(--text-muted);padding:1rem;font-size:0.9rem;">No active alerts — site is clear.</p>`;
            return;
        }

        log.innerHTML = alerts.map(renderLogItem).join("");

        // Wire up Ack buttons
        log.querySelectorAll(".ack-btn").forEach(btn => {
            btn.addEventListener("click", () => acknowledgeAlert(btn.dataset.id));
        });

        // UC-03: if we arrived via a dashboard alert click, scroll to + flash that alert
        if (_HIGHLIGHT_ID) {
            const target = document.getElementById(`log-${_HIGHLIGHT_ID}`);
            if (target) {
                target.scrollIntoView({ behavior: "smooth", block: "center" });
                // Flash border to draw attention
                target.style.transition = "outline 0s";
                target.style.outline    = "2px solid var(--accent-red)";
                setTimeout(() => {
                    target.style.transition = "outline 1.5s ease";
                    target.style.outline    = "2px solid transparent";
                }, 1500);
            }
        } else {
            // Default: scroll to first critical alert
            const firstCrit = log.querySelector(".log-item.critical");
            if (firstCrit) firstCrit.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }

    } catch (err) {
        console.error("[Safety] Alert log error:", err);
        const log = document.getElementById("alertLog");
        if (log) log.innerHTML = `<p style="color:var(--accent-red);padding:1rem;font-size:0.85rem;">Failed to load alerts. Check your connection.</p>`;
    }
}

/* ================================================================== */
/*  Acknowledge                                                         */
/* ================================================================== */

async function acknowledgeAlert(alertId) {
    try {
        await apiFetch(safetyUrl(`/api/safety/alerts/${alertId}/acknowledge`), { method: "POST" });
        showToast?.("Alert acknowledged.", "success");
        loadAlertLog();
        loadZones();
    } catch {
        showToast?.("Could not acknowledge alert.", "error");
    }
}

/* ================================================================== */
/*  Zone status cards                                                   */
/* ================================================================== */

const ZONE_STATUS_COLOURS = {
    clear:    "var(--accent-green)",
    warning:  "var(--accent-orange)",
    critical: "var(--accent-red)",
};

function renderZoneCard(zone) {
    const colour = ZONE_STATUS_COLOURS[zone.status] ?? "var(--text-secondary)";
    return `
    <div class="zone-card">
        <div class="zone-camera text-muted" style="font-size:0.75rem">${zone.camera}</div>
        <div class="zone-name" style="font-weight:600;margin:4px 0">${zone.name}</div>
        <div style="color:${colour};font-size:0.8rem;font-weight:700;text-transform:uppercase">
            ${zone.status}
            ${zone.active_alerts > 0 ? `· ${zone.active_alerts} alert${zone.active_alerts > 1 ? "s" : ""}` : "· Clear"}
        </div>
    </div>`;
}

async function loadZones() {
    const container = document.getElementById("zonesContainer");
    if (!container) return;

    try {
        const json = await apiFetch(safetyUrl("/api/safety/zones"));
        container.innerHTML = json.data.map(renderZoneCard).join("");
    } catch (err) {
        console.error("[Safety] Zones error:", err);
    }
}

/* ================================================================== */
/*  Real-time SSE updates                                               */
/* ================================================================== */

function connectSafetySse() {
    if (typeof EventSource === "undefined") return;

    const es = new EventSource("/api/events");

    es.onmessage = event => {
        try {
            const payload = JSON.parse(event.data);
            if (payload.type === "dashboard_update") {
                loadAlertLog();
                loadZones();
            }
        } catch (e) { /* ignore parse errors */ }
    };

    es.onerror = () => {
        es.close();
        setTimeout(connectSafetySse, 5000);
    };
}

/* ================================================================== */
/*  Init                                                                */
/* ================================================================== */
document.addEventListener("DOMContentLoaded", async () => {
    await loadSafetyProjectSwitcher();
    updateSafetyNavLinks();
    CAM.start();
    loadAlertLog();
    loadZones();
    connectSafetySse();

    // Polling fallback
    setInterval(loadAlertLog, 15_000);
});