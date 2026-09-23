"""Does zero-shot labelling actually recognise what is in a picture?

Real CLIP, real images. The pictures are drawn rather than photographed —
there is no photo library on this machine — so this measures whether the
mechanism works and discriminates, NOT how it performs on photographs. A
drawn beach is an easier problem than a photographed one, and the numbers
here should be read that way.

What it does establish, which is the thing worth establishing: that the
embeddings are real, that different images produce different labels, that the
margin rule keeps quiet on a blank image, and that nothing crashes.
"""
from PIL import Image, ImageDraw

from app import vision

FAIL = []


def check(ok, label, extra=''):
    print('  %-52s %s %s' % (label, 'PASS' if ok else 'FAIL', extra))
    if not ok:
        FAIL.append(label)


def beach():
    im = Image.new('RGB', (512, 512), (126, 192, 238))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 300, 512, 380], fill=(64, 140, 200))      # sea
    d.rectangle([0, 380, 512, 512], fill=(228, 208, 158))     # sand
    d.ellipse([400, 40, 470, 110], fill=(255, 236, 150))      # sun
    return im


def forest():
    im = Image.new('RGB', (512, 512), (168, 205, 232))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 360, 512, 512], fill=(86, 128, 70))
    for x in range(20, 512, 70):
        d.polygon([(x, 340), (x + 30, 170), (x + 60, 340)], fill=(38, 92, 56))
        d.rectangle([x + 25, 340, x + 36, 380], fill=(92, 64, 42))
    return im


def night_city():
    im = Image.new('RGB', (512, 512), (12, 14, 34))
    d = ImageDraw.Draw(im)
    for x in range(30, 500, 60):
        h = 120 + (x % 170)
        d.rectangle([x, 512 - h, x + 44, 512], fill=(26, 30, 58))
        for y in range(512 - h + 12, 500, 26):
            d.rectangle([x + 8, y, x + 16, y + 10], fill=(250, 226, 140))
    return im


def blank():
    return Image.new('RGB', (512, 512), (128, 128, 128))


print('  clip available:', vision.clip_available())
check(vision.clip_available(), 'CLIP is available, so this is real inference')

names, mat = vision._label_matrix()
check(mat is not None and len(names) == len(vision.LABELS),
      'a text embedding exists for every label', None if mat is None else mat.shape)

print()
print('  --- what the model says about each picture ---')
results = {}
for name, maker in (('beach', beach), ('forest', forest),
                    ('night city', night_city), ('blank grey', blank)):
    vec = vision.embed_image(maker())
    labs = vision.label_image(vec)
    results[name] = [l for l, _ in labs]
    shown = ', '.join('%s %.3f' % (l, s) for l, s in labs) or '(nothing)'
    print('  %-12s -> %s' % (name, shown))

print()
check(bool(results['beach']), 'the beach picture produced labels')
check(any(l in results['beach'] for l in ('beach', 'water', 'sky', 'sunset')),
      'and they are about a beach', results['beach'])
check(any(l in results['forest'] for l in ('forest', 'tree', 'garden', 'mountain')),
      'the forest picture is about trees', results['forest'])
check(any(l in results['night city'] for l in ('night', 'city', 'building')),
      'the night city picture is about a city at night', results['night city'])

check(results['beach'] != results['forest'],
      'different pictures get different labels')
check(len(results['blank grey']) <= 2,
      'a blank image produces little or nothing', results['blank grey'])

print()
print('  --- the guards ---')
check(vision.label_image(None) == [], 'a missing vector returns no labels')
check(all(len(vision.label_image(vision.embed_image(m()))) <= vision.LABEL_MAX
          for m in (beach, forest, night_city)),
      'never more than LABEL_MAX labels', vision.LABEL_MAX)
scores = [s for _, s in vision.label_image(vision.embed_image(beach()))]
check(scores == sorted(scores, reverse=True), 'labels come back strongest first')

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
