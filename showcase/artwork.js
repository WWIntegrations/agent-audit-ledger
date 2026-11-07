/** Original two-link illustration. Shading and grain are authored, not reference assets. */
export function createArtwork(canvas) {
    const context = canvas.getContext('2d');
    if (!context)
        return null;
    let width = 0, height = 0;
    const grain = document.createElement('canvas');
    grain.width = grain.height = 256;
    const gc = grain.getContext('2d');
    if (!gc)
        return null;
    const pixels = gc.createImageData(256, 256);
    let seed = 982451653;
    for (let i = 0; i < pixels.data.length; i += 4) {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        const light = seed >>> 24;
        pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = light > 125 ? 255 : 0;
        pixels.data[i + 3] = light > 215 ? 105 : light < 40 ? 65 : 0;
    }
    gc.putImageData(pixels, 0, 0);
    function link(cx, cy, length, breadth, angle, thickness) {
        context.save();
        context.translate(cx, cy);
        context.rotate(angle);
        const path = new Path2D();
        path.roundRect(-length / 2, -breadth / 2, length, breadth, breadth / 2);
        context.lineJoin = context.lineCap = 'round';
        const shade = context.createLinearGradient(0, -breadth / 2 - thickness / 2, 0, breadth / 2 + thickness / 2);
        shade.addColorStop(0, '#92e0ff');
        shade.addColorStop(.14, '#45b5ff');
        shade.addColorStop(.34, '#0877d9');
        shade.addColorStop(.52, '#003467');
        shade.addColorStop(.67, '#07569c');
        shade.addColorStop(.84, '#54c6ff');
        shade.addColorStop(1, '#002e5e');
        context.lineWidth = thickness;
        context.strokeStyle = shade;
        context.stroke(path);
        // Fine specular ridge and granular material are clipped to this original link.
        context.save();
        context.translate(0, -thickness * .16);
        context.lineWidth = thickness * .62;
        context.strokeStyle = 'rgba(131,219,255,.14)';
        context.stroke(path);
        context.restore();
        const layer = document.createElement('canvas');
        layer.width = Math.ceil(length + thickness + 20);
        layer.height = Math.ceil(breadth + thickness + 20);
        const lc = layer.getContext('2d');
        if (lc) {
            lc.translate(layer.width / 2, layer.height / 2);
            lc.strokeStyle = lc.createPattern(grain, 'repeat');
            lc.lineWidth = thickness;
            lc.stroke(path);
            context.drawImage(layer, -layer.width / 2, -layer.height / 2);
        }
        context.restore();
    }
    function resize() {
        const rect = canvas.getBoundingClientRect();
        width = rect.width;
        height = rect.height;
        const ratio = Math.min(devicePixelRatio || 1, width < 700 ? 1.25 : 1.5);
        canvas.width = Math.max(1, Math.round(width * ratio));
        canvas.height = Math.max(1, Math.round(height * ratio));
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.clearRect(0, 0, width, height);
        const mobile = width < 700;
        const scale = mobile ? width / 390 : width / 1440;
        context.save();
        context.scale(scale, scale);
        if (mobile) {
            link(205, 290, 630, 190, -.72, 90);
            link(160, 790, 650, 210, -.72, 105);
        }
        else {
            link(620, 170, 1160, 450, -.56, 185);
            link(880, 940, 1300, 390, -.64, 190);
        }
        context.restore();
    }
    resize();
    return {
        resize, stats: () => ({
            renderer: 'Canvas2D', width, height
        }), dispose() {
        }
    };
}
