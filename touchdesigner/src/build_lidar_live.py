# build_lidar_live.py - buduje komponent /project1/lidar_live w TouchDesignerze.
# Wykonywany WEWNATRZ TD (MCP 'run' albo Textport). Przed wykonaniem musi byc ustawiona
# zmienna SRC_SHM (tekst modulu app/l2_shm.py).
#
# Architektura: l2_server.py (osobny proces) odbiera lidar i pisze do pamieci wspoldzielonej;
# TD tylko kopiuje bufor do tekstur, a wiek/wygaszanie/kolory liczy GPU (GLSL TOP).

PARENT = op('/project1')
W, H = 512, 256                      # tekstura punktow: 131072 punktow

old = PARENT.op('lidar_live')
if old:
    try:
        old.par.Active = False
    except Exception:
        pass
    old.destroy()

L = PARENT.create(baseCOMP, 'lidar_live')
L.nodeX, L.nodeY = 0, -300
L.comment = 'Unitree L2 na zywo: ~60 tys. pkt/s, plynne wygaszanie (GPU), tlo i wykrywanie ludzi'
L.color = (0.2, 0.45, 0.6)


def mk(typ, name, x, y, L=L):
    o = L.create(typ, name)
    o.nodeX, o.nodeY = x, y
    return o


# ---------------------------------------------------------------- parametry
page = L.appendCustomPage('Lidar')
def P(kind, name, label, val=None, lo=None, hi=None, menu=None, page=page):
    p = getattr(page, 'append' + kind)(name, label=label)[0]
    if menu:
        p.menuNames = menu
        p.menuLabels = menu
    if lo is not None:
        p.normMin, p.normMax = lo, hi
    if val is not None:
        p.default = val
        p.val = val
    return p

P('Toggle', 'Active', 'Aktywny', True)
P('Float', 'Persistence', 'Czas zycia punktu [s]', 1.5, 0.1, 2.0)
P('Float', 'Pointsize', 'Rozmiar punktu [m]', 0.018, 0.003, 0.08)
P('Float', 'Fade', 'Zmniejszanie starych punktow', 0.8, 0.0, 1.0)
P('Float', 'Dim', 'Przyciemnianie starych punktow', 0.5, 0.0, 1.0)
P('Menu', 'Colormode', 'Kolor', 'intensity', menu=['intensity', 'height', 'foreground', 'white'])
P('Float', 'Intmin', 'Intensywnosc: min', 0.33, 0.0, 1.0)
P('Float', 'Intmax', 'Intensywnosc: max', 1.0, 0.0, 1.0)
P('Float', 'Heightmin', 'Wysokosc: min [m]', -0.5, -3.0, 5.0)
P('Float', 'Heightmax', 'Wysokosc: max [m]', 3.0, -3.0, 5.0)
P('Toggle', 'Fgonly', 'Tylko nowe obiekty (wymaga tla)', False)
P('Float', 'Orbitspeed', 'Predkosc orbity kamery', 0.08, 0.0, 0.5)

page2 = L.appendCustomPage('Tlo i ludzie')
P('Pulse', 'Learnbg', 'Naucz tlo (pusta scena!)', page=page2)
P('Float', 'Learnseconds', 'Czas nauki tla [s]', 3.0, 1.0, 10.0, page=page2)
P('Pulse', 'Clearbg', 'Wyczysc tlo', page=page2)
P('Toggle', 'Topfg', 'Bloby: tylko nowe obiekty (wymaga tla)', True, page=page2)
P('Int', 'Topres', 'Rozdzielczosc mapy z gory', 128, 32, 512, page=page2)
P('Float', 'Extent', 'Zasieg mapy (polowa boku) [m]', 5.0, 1.0, 20.0, page=page2)
P('Float', 'Zmin', 'Mapa: min wysokosc [m]', -0.5, -3.0, 3.0, page=page2)
P('Float', 'Zmax', 'Mapa: max wysokosc [m]', 1.5, -3.0, 3.0, page=page2)

page3 = L.appendCustomPage('Serwer')
P('Toggle', 'Autostart', 'Sam uruchamiaj serwer', True, page=page3)
P('Folder', 'Kitpath', 'Folder paczki (puste = z instalatora)', '', page=page3)
P('Str', 'Localip', 'IP komputera', '192.168.1.2', page=page3)
P('Int', 'Localport', 'Port UDP', 6201, page=page3)
P('Float', 'Rangemin', 'Min odleglosc [m]', 0.25, 0.0, 5.0, page=page3)
P('Float', 'Rangemax', 'Max odleglosc [m]', 40.0, 1.0, 40.0, page=page3)
P('Float', 'Voxel', 'Rozdzielczosc tla [m]', 0.10, 0.03, 0.3, page=page3)
P('Pulse', 'Restart', 'Restart serwera', page=page3)

# ---------------------------------------------------------------- moduly
shm_dat = mk(textDAT, 'l2_shm', -400, 200)
shm_dat.text = SRC_SHM

status = mk(textDAT, 'status', 200, 400)
status.text = 'start'

control = mk(executeDAT, 'control', 0, 200)
control.text = '''# Polaczenie z l2_server.py przez pamiec wspoldzielona + odswiezanie tekstur co klatke.
import sys
import time

_REG = '_unitree_l2_clients'


def getClient():
	reg = sys.__dict__.setdefault(_REG, {})
	key = me.parent().path
	c = reg.get(key)
	if c is None:
		c = op('l2_shm').module.Client()
		reg[key] = c
	return c


def setStatus(msg):
	s = op('status')
	if s.text != msg:
		s.text = msg


def serverArgs():
	c = me.parent()
	return ['--local-ip', c.par.Localip.eval(), '--local-port', str(int(c.par.Localport.eval())),
	        '--range-min', str(c.par.Rangemin.eval()), '--range-max', str(c.par.Rangemax.eval()),
	        '--voxel', str(c.par.Voxel.eval()),
	        '--bg-file', project.folder + '/lidar_background.npy']


def restartServer():
	cl = getClient()
	cl.request_quit()
	cl.detach()
	cl.launched_at = time.time() - 13      # nowe uruchomienie za ~2 s
	setStatus('restart serwera...')


def onExit():
	getClient().detach()                    # serwer z --auto-exit sam sie zamknie po 10 s


def onFrameStart(frame):
	L = me.parent()
	cl = getClient()
	if not L.par.Active.eval():
		if cl.attached:
			cl.detach()
		setStatus('wylaczony (Active)')
		return
	now = time.time()
	if not cl.attached and not cl.attach():
		if L.par.Autostart.eval() and now - cl.launched_at > 15:
			setStatus(cl.launch(L.par.Kitpath.eval() or None, serverArgs()))
		return
	if not cl.server_alive():
		setStatus('serwer nie odpowiada - ponawiam...')
		if now - cl.launched_at > 15:
			cl.detach()
		return

	persist = float(L.par.Persistence.eval())
	cl.heartbeat(persist=persist, zmin=L.par.Zmin.eval(), zmax=L.par.Zmax.eval(),
	             extent=L.par.Extent.eval(), topres=int(L.par.Topres.eval()),
	             topfg=float(L.par.Topfg.eval()))
	# uniformy shaderow
	uP = (cl.now(), persist, float(L.par.Fade.eval()), float(L.par.Fgonly.eval()))
	mode = ('intensity', 'height', 'foreground', 'white').index(L.par.Colormode.eval())
	uC = (mode, float(L.par.Intmin.eval()), float(L.par.Intmax.eval()), float(L.par.Dim.eval()))
	uH = (float(L.par.Heightmin.eval()), float(L.par.Heightmax.eval()), 0.0, 0.0)
	for g, us in ((op('pos'), (uP,)), (op('col'), (uP, uC, uH))):
		for i, u in enumerate(us):
			for comp_, v in zip('xyzw', u):
				setattr(g.par, 'vec%dvalue%s' % (i, comp_), v)
	for n in ('ring_pos', 'ring_attr', 'topdown', 'info', 'imu'):
		op(n).cook(force=True)
	if frame % 15 == 0:
		H = op('l2_shm').module.H
		setStatus('OK | %d pkt/s | %d punktow | %s' % (cl.hdr[H['RATE']], cl.hdr[H['N_ALIVE']], cl.status()))
'''
control.par.exit = True
control.par.framestart = True

parexec = mk(parameterexecuteDAT, 'parexec', 200, 200)
parexec.par.op = L
parexec.par.pars = '*'
parexec.par.valuechange = True
parexec.par.onpulse = True
parexec.par.builtin = False
parexec.text = '''RESTART_PARS = ('Localip', 'Localport', 'Rangemin', 'Rangemax', 'Voxel', 'Kitpath')

def onValueChange(par, prev):
	if par.name in RESTART_PARS:
		op('control').module.restartServer()
	return

def onPulse(par):
	ctl = op('control').module
	cl = ctl.getClient()
	if par.name == 'Restart':
		ctl.restartServer()
	elif not cl.attached:
		return
	elif par.name == 'Learnbg':
		cl.learn_background(float(parent().par.Learnseconds.eval()))
	elif par.name == 'Clearbg':
		cl.clear_background()
	return
'''

# ---------------------------------------------------------------- Script TOP/CHOP
def script_op(typ, name, x, y, body):
    cb = mk(textDAT, name + '_script', x, y - 150)
    cb.text = ('def onSetupParameters(scriptOp):\n\treturn\n\n'
               'def onCook(scriptOp):\n\tcl = op("control").module.getClient()\n'
               '\tif not cl.attached:\n\t\treturn\n' + body)
    o = mk(typ, name, x, y)
    o.par.callbacks = cb
    return o

ring_a = script_op(scriptTOP, 'ring_pos', -200, -100,
    f'\tscriptOp.copyNumpyArray(cl.ring_a.reshape({H}, {W}, 4))\n')
ring_b = script_op(scriptTOP, 'ring_attr', -200, -300,
    f'\tscriptOp.copyNumpyArray(cl.ring_b.reshape({H}, {W}, 4))\n')
top = script_op(scriptTOP, 'topdown', 400, -100,
    '\tt = cl.topdown()\n\tif t is not None:\n\t\tscriptOp.copyNumpyArray(t)\n')
for t in (ring_a, ring_b, top):
    t.par.format = 'rgba32float'

info = script_op(scriptCHOP, 'info', 600, -100,
    '\tH = op("l2_shm").module.H\n\th = cl.hdr\n\tscriptOp.clear()\n\tscriptOp.numSamples = 1\n'
    '\tvals = dict(points=h[H["N_ALIVE"]], points_per_s=h[H["RATE"]], foreground=h[H["N_FG"]],'
    ' has_background=h[H["HAS_BG"]], learning=h[H["LEARNING"]], map_ms=h[H["MAP_MS"]],'
    ' server_ok=float(cl.server_alive()))\n'
    '\tfor k, v in vals.items():\n\t\tscriptOp.appendChan(k)[0] = float(v)\n')

imu = script_op(scriptCHOP, 'imu', 800, -100,
    '\tscriptOp.clear()\n\tscriptOp.numSamples = 1\n'
    '\tfor k, v in cl.imu().items():\n\t\tscriptOp.appendChan(k)[0] = float(v)\n')

GLSL_HEAD = '''// a = ring_pos: x y z (osie TD), czas przyjscia [s]
// b = ring_attr: intensywnosc 0..1, fg 0/1
// uP = (teraz [s], czas zycia [s], fade, tylko_fg)
uniform vec4 uP;
out vec4 fragColor;
'''
POS_SHADER = GLSL_HEAD + '''
void main() {
	ivec2 ij = ivec2(gl_FragCoord.xy);
	vec4 a = texelFetch(sTD2DInputs[0], ij, 0);
	vec4 b = texelFetch(sTD2DInputs[1], ij, 0);
	float age = uP.x - a.w;
	float life = 1.0 - age / uP.y;
	float alive = (age > -0.2 && life > 0.0) ? 1.0 : 0.0;
	if (uP.w > 0.5) alive *= step(0.5, b.y);
	float s = alive * (1.0 - uP.z * (1.0 - clamp(life, 0.0, 1.0)));
	fragColor = TDOutputSwizzle(vec4(a.xyz, s));
}
'''
COL_SHADER = GLSL_HEAD + '''// uC = (tryb 0 intens. / 1 wysokosc / 2 nowe obiekty / 3 bialy, int_min, int_max, przyciemnianie)
// uH = (wys_min, wys_max) [m]
uniform vec4 uC;
uniform vec4 uH;

vec3 turbo(float x) {   // Google "Turbo" (aproksymacja wielomianowa)
	const vec4 kR = vec4(0.13572138, 4.61539260, -42.66032258, 132.13108234);
	const vec4 kG = vec4(0.09140261, 2.19418839, 4.84296658, -14.18503333);
	const vec4 kB = vec4(0.10667330, 12.64194608, -60.58204836, 110.36276771);
	const vec2 kR2 = vec2(-152.94239396, 59.28637943);
	const vec2 kG2 = vec2(4.27729857, 2.82956604);
	const vec2 kB2 = vec2(-89.90310912, 27.34824973);
	x = clamp(x, 0.0, 1.0);
	vec4 v4 = vec4(1.0, x, x * x, x * x * x);
	vec2 v2 = v4.zw * v4.z;
	return vec3(dot(v4, kR) + dot(v2, kR2), dot(v4, kG) + dot(v2, kG2), dot(v4, kB) + dot(v2, kB2));
}

void main() {
	ivec2 ij = ivec2(gl_FragCoord.xy);
	vec4 a = texelFetch(sTD2DInputs[0], ij, 0);
	vec4 b = texelFetch(sTD2DInputs[1], ij, 0);
	float life = clamp(1.0 - (uP.x - a.w) / uP.y, 0.0, 1.0);
	int m = int(uC.x + 0.5);
	vec3 c;
	if (m == 0)      c = turbo((b.x - uC.y) / max(uC.z - uC.y, 1e-4));
	else if (m == 1) c = turbo((a.y - uH.x) / max(uH.y - uH.x, 1e-4));
	else if (m == 2) c = mix(vec3(0.35, 0.38, 0.45), vec3(1.0, 0.25, 0.1), b.y);
	else             c = vec3(1.0);
	c *= mix(1.0, life, uC.w);
	fragColor = TDOutputSwizzle(vec4(c, life));
}
'''

def glsl(name, x, y, shader, uniforms, L=L, ring_a=ring_a, ring_b=ring_b):
    g = mk(glslTOP, name, x, y)
    g.inputConnectors[0].connect(ring_a)
    g.inputConnectors[1].connect(ring_b)
    g.par.format = 'rgba32float'
    g.par.inputfiltertype = 'nearest'
    pix = g.par.pixeldat.eval()
    pix.text = shader
    g.seq.vec.numBlocks = len(uniforms)
    for i, u in enumerate(uniforms):
        setattr(g.par, f'vec{i}name', u)
    return g

pos = glsl('pos', 0, -100, POS_SHADER, ['uP'])
col = glsl('col', 200, -100, COL_SHADER, ['uP', 'uC', 'uH'])

# ---------------------------------------------------------------- mapa z gory + ludzie
top_view = mk(resolutionTOP, 'topdown_view', 400, -350)
top_view.inputConnectors[0].connect(top)
top_view.par.outputresolution = 'custom'
top_view.par.resolutionw = 512
top_view.par.resolutionh = 512
top_view.par.inputfiltertype = 'nearest'

blur = mk(blurTOP, 'topdown_blur', 600, -350)
blur.inputConnectors[0].connect(top_view)
blur.par.size = 6
blob = mk(blobtrackTOP, 'blobs', 800, -350)
blob.inputConnectors[0].connect(blur)
blob.par.threshold = 0.25
blob.par.minblobsize = 0.03
# mapa z gory: R = wszystkie punkty (podglad), G = nowe obiekty -> bloby z G
blob.par.monosource.expr = "'green' if parent().par.Topfg else 'red'"
blob.par.drawblobs = True
blob_info = mk(infoDAT, 'blob_table', 1000, -350)
blob_info.par.op = blob

# ---------------------------------------------------------------- 3D
mat = mk(constantMAT, 'mat', 0, -600)
geo = mk(geometryCOMP, 'geo', 200, -600)
for ch in list(geo.children):
    ch.destroy()
box = geo.create(boxSOP, 'point')
for p_ in (box.par.sizex, box.par.sizey, box.par.sizez):
    p_.expr = 'parent(2).par.Pointsize'
box.render = box.display = True
geo.par.material = mat
geo.par.instancing = True
geo.par.instanceop = pos
geo.par.instancecountmode = 'manual'
geo.par.numinstances = W * H          # martwe punkty maja skale 0 (liczy shader 'pos')
geo.par.instancetx, geo.par.instancety, geo.par.instancetz = 'r', 'g', 'b'
geo.par.instancesx = geo.par.instancesy = geo.par.instancesz = 'a'
geo.par.instancecolorop = col
geo.par.instancer, geo.par.instanceg, geo.par.instanceb = 'r', 'g', 'b'

imu_geo = mk(geometryCOMP, 'imu_box', 400, -600)
for ch in list(imu_geo.children):
    ch.destroy()
ib = imu_geo.create(boxSOP, 'box')
ib.par.sizex, ib.par.sizey, ib.par.sizez = 0.12, 0.08, 0.16
ib.render = ib.display = True
imu_mat = mk(constantMAT, 'imu_mat', 400, -750)
imu_mat.par.colorr, imu_mat.par.colorg, imu_mat.par.colorb = 1, 0.85, 0.1
imu_geo.par.material = imu_mat
imu_geo.par.rx.expr = "op('imu')['roll']"
imu_geo.par.rz.expr = "-op('imu')['pitch']"
imu_geo.par.ry.expr = "op('imu')['yaw']"

target = mk(nullCOMP, 'target', 600, -750)
target.par.ty = 1.2
cam = mk(cameraCOMP, 'cam', 600, -600)
cam.par.tx.expr = "7 * math.sin(absTime.seconds * parent().par.Orbitspeed)"
cam.par.tz.expr = "7 * math.cos(absTime.seconds * parent().par.Orbitspeed)"
cam.par.ty = 4
cam.par.lookat = target
cam.par.fov = 55

render = mk(renderTOP, 'render', 800, -600)
render.par.camera = cam
render.par.geometry = 'geo imu_box'
render.par.outputresolution = 'custom'
render.par.resolutionw, render.par.resolutionh = 1280, 720
render.par.bgcolorr, render.par.bgcolorg, render.par.bgcolorb, render.par.bgcolora = 0.03, 0.03, 0.045, 1
render.par.antialias = 'aa4x'
out = mk(nullTOP, 'out', 1000, -600)
out.inputConnectors[0].connect(render)

for o in (out, top_view, blob, info):
    o.viewer = True

print('zbudowano', L.path)
