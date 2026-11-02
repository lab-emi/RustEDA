# klayout -b -r strip_prboundary.py -rd src=... -rd out=...
import pya, hashlib
ly = pya.Layout(); ly.read(src)
li = ly.find_layer(189, 4)
n = 0
if li is not None:
    for c in ly.each_cell():
        n += c.shapes(li).size(); c.shapes(li).clear()
    ly.delete_layer(li)
opt = pya.SaveLayoutOptions(); opt.write_context_info = False
ly.write(out, opt)
print("removed", n, "shapes; cells", ly.cells(), hashlib.sha256(open(out,'rb').read()).hexdigest())
