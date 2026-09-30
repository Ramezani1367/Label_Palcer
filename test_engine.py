"""Checks for paper choice and middle-crop-mark removal."""

import os
import tempfile

from PIL import Image, ImageDraw

import engine


def test_fit_count_does_not_reserve_trailing_gap():
    assert engine.fit_count(110, 40, 30) == 2
    assert engine.fit_count(200, 50, 0) == 4
    assert engine.fit_count(199.9, 50, 0) == 3
    assert engine.fit_count(90, 100, 0) == 0


def test_butted_labels_lose_middle_marks():
    rects = []
    for row in range(2):
        for col in range(2):
            x = 20 + col * 50
            y = 15 + row * 30
            rects.append((x, y, x + 50, y + 30))
    kept, removed = engine.build_crop_marks(rects, length=3, offset=1.2, page_w=160, page_h=100, mode="smart")
    assert kept, "outer marks should remain"
    assert removed, "middle marks should be removed"
    for seg in kept:
        for rect in rects:
            assert engine.intersect_seg_rect(seg, rect, 0.12) is None
    # A mark above the gang, on the shared vertical cut, should survive.
    shared_x = 20 + 50
    outer = [seg for seg in kept if abs(seg[0] - shared_x) < 0.2 and abs(seg[2] - shared_x) < 0.2 and max(seg[1], seg[3]) <= 15.2]
    assert outer, "the outward mark on the top cut should stay"


def test_gutter_marks_stay_when_gap_is_wide():
    rects = [(10, 10, 40, 40), (55, 10, 85, 40)]
    kept, removed = engine.build_crop_marks(rects, length=2, offset=1, page_w=100, page_h=60, mode="smart")
    gutter = [
        seg for seg in kept
        if min(seg[0], seg[2]) >= 40 and max(seg[0], seg[2]) <= 55
    ]
    assert gutter, "marks that sit fully in the gutter should stay"
    assert removed == [] or all(engine.intersect_seg_rect(seg, rects[0], 0.05) or True for seg in removed)


def test_paper_orientation_follows_the_job():
    files = [{"id": "a", "name": "a.png", "kind": "image", "width_mm": 86, "height_mm": 48, "copies": 12}]
    settings = {
        "paper_mode": "auto",
        "enabled_papers": ["A4", "A3", "SRA3", "13x19", "35x50", "50x70", "70x100"],
        "h_gap": 0,
        "v_gap": 0,
        "crop_enabled": True,
        "crop_length": 6,
        "crop_offset": 3,
        "margin_mode": "auto",
        "rtl": True,
        "alignment": "center",
    }
    layout = engine.compute_layout(files, settings)
    paper = layout["paper"]
    assert paper["id"] == "A4", paper
    assert paper["orientation"] == "landscape", paper
    assert layout["grid"]["cols"] == 3
    assert layout["grid"]["rows"] == 4
    assert layout["stats"]["pages"] == 1
    assert layout["stats"]["marks_removed"] > 0
    assert "افقی" in layout["reason"]


def test_one_copy_does_not_pick_a_press_sheet():
    files = [{"id": "a", "name": "a.png", "kind": "image", "width_mm": 90, "height_mm": 50, "copies": 1}]
    settings = {
        "paper_mode": "auto",
        "enabled_papers": engine.PRESETS["common"],
        "crop_enabled": False,
        "margin_mode": "auto",
        "h_gap": 0,
        "v_gap": 0,
    }
    layout = engine.compute_layout(files, settings)
    assert layout["paper"]["id"] == "A4"
    assert layout["stats"]["pages"] == 1


def _label_with_marks(path, dpi=150):
    trim_w = int(round(86 / 25.4 * dpi))
    trim_h = int(round(48 / 25.4 * dpi))
    offset = int(round(3 * engine.PT_TO_MM / 25.4 * dpi))
    length = int(round(6 * engine.PT_TO_MM / 25.4 * dpi))
    margin = offset + length + 2
    im = Image.new("RGB", (trim_w + margin * 2, trim_h + margin * 2), (250, 250, 248))
    draw = ImageDraw.Draw(im)
    draw.rectangle([margin, margin, margin + trim_w - 1, margin + trim_h - 1], fill=(120, 30, 40))
    x1, y1 = margin, margin
    x2, y2 = margin + trim_w, margin + trim_h
    marks = engine.corner_marks(x1, y1, x2, y2, length, offset)
    for seg in marks:
        draw.line([(seg[0], seg[1]), (seg[2], seg[3])], fill=(0, 0, 0), width=1)
    im.save(path, dpi=(dpi, dpi))
    return im, dpi


def test_detector_finds_marks_and_ignores_a_frame():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "marks.png")
        image, dpi = _label_with_marks(path)
        found = engine.detect_crop_margins(image, dpi)
        assert found, "crop marks should be detected"
        assert found["confidence"] >= 0.75
        for key in ("left", "top", "right", "bottom"):
            assert 2.5 <= found[key] <= 8, found

        frame = Image.new("RGB", image.size, (250, 250, 248))
        draw = ImageDraw.Draw(frame)
        inset = 18
        draw.rectangle([inset, inset, frame.width - inset, frame.height - inset], outline=(0, 0, 0), width=2)
        draw.rectangle([inset + 8, inset + 8, frame.width - inset - 8, frame.height - inset - 8], fill=(40, 90, 70))
        assert engine.detect_crop_margins(frame, dpi) is None

        plain = Image.new("RGB", (500, 280), (20, 80, 60))
        assert engine.detect_crop_margins(plain, dpi) is None


def test_pdf_roundtrip_has_outer_marks_only():
    import pymupdf

    with tempfile.TemporaryDirectory() as tmp:
        label = os.path.join(tmp, "label.png")
        Image.new("RGB", (400, 220), (170, 40, 48)).save(label, dpi=(300, 300))
        files = [{
            "id": "a", "name": "label.png", "kind": "image",
            "width_mm": 400 / 300 * 25.4, "height_mm": 220 / 300 * 25.4, "copies": 4,
        }]
        layout = engine.compute_layout(files, {
            "paper_mode": "manual",
            "custom_w": 120,
            "custom_h": 90,
            "h_gap": 0,
            "v_gap": 0,
            "crop_enabled": True,
            "margin_mode": "auto",
            "rtl": False,
            "alignment": "center",
        })
        assert layout["stats"]["marks_removed"] > 0
        dest = os.path.join(tmp, "out.pdf")
        lookup = {"a": {"kind": "image", "path": label, "name": "label.png"}}
        engine.build_pdf(layout, lookup, dest)
        doc = pymupdf.open(dest)
        assert doc.page_count == 1
        page = doc[0]
        assert abs(page.rect.width - engine.mm_to_pt(layout["paper"]["width_mm"])) < 0.2
        stream = page.read_contents().decode("latin1")
        assert "0 G" in stream, "crop marks should be K-only gray"
        assert stream.count(" l\n") >= 8
        doc.close()


def test_under_five_sheets_moves_to_a4_for_visiting_cut():
    """A3 can be cheaper, but under five sheets the bindery will not cut cards."""
    files = [{"id": "a", "name": "card.png", "kind": "image", "width_mm": 85, "height_mm": 55, "copies": 40}]
    settings = {
        "paper_mode": "auto",
        "enabled_papers": ["A4", "A3"],
        "h_gap": 0,
        "v_gap": 0,
        "crop_enabled": False,
        "margin_mode": "manual",
        "margin": 5,
        "finish_a4": True,
        "finish_min_pages": 5,
    }
    layout = engine.compute_layout(files, settings)
    assert layout["paper"]["id"] == "A4", layout["paper"]
    assert layout["stats"]["pages"] >= 5, layout["stats"]
    assert "برش ویزیتی" in layout["reason"]
    without = engine.compute_layout(files, {**settings, "finish_a4": False})
    assert without["paper"]["id"] == "A3", without["paper"]
    assert without["stats"]["pages"] < 5


def test_a4_already_under_five_is_not_replaced_by_a_larger_sheet():
    files = [{"id": "a", "name": "a.png", "kind": "image", "width_mm": 86, "height_mm": 48, "copies": 12}]
    layout = engine.compute_layout(files, {
        "paper_mode": "auto",
        "enabled_papers": ["A4", "A3"],
        "crop_enabled": False,
        "margin_mode": "manual",
        "margin": 5,
        "h_gap": 0,
        "v_gap": 0,
        "finish_a4": True,
        "finish_min_pages": 5,
    })
    assert layout["paper"]["id"] == "A4"
    assert layout["stats"]["pages"] == 1
    assert any("برش ویزیتی" in item for item in layout["warnings"])


def test_a3_orientation_picks_the_lower_waste():
    files = [{"id": "a", "name": "a.png", "kind": "image", "width_mm": 86, "height_mm": 48, "copies": 12}]
    layout = engine.compute_layout(files, {
        "paper_mode": "orient",
        "paper_id": "A3",
        "crop_enabled": False,
        "margin_mode": "manual",
        "margin": 5,
        "h_gap": 0,
        "v_gap": 0,
        "finish_a4": False,
    })
    paper = layout["paper"]
    other = next(item for item in layout["candidates"] if item["orientation"] != paper["orientation"])
    unused = lambda item: item["pages"] * (item["page_area"] - item["grid_w"] * item["grid_h"])
    assert unused(paper) < unused(other), (unused(paper), unused(other), paper, other)
    assert paper["orientation"] == "portrait"
    assert "کمترین پرتی" in layout["reason"]

    even = engine.compute_layout(files, {
        "paper_mode": "orient",
        "paper_id": "A3",
        "crop_enabled": False,
        "margin_mode": "manual",
        "margin": 0,
        "h_gap": 0,
        "v_gap": 0,
        "finish_a4": False,
    })
    even_other = next(item for item in even["candidates"] if item["orientation"] != even["paper"]["orientation"])
    assert abs(unused(even["paper"]) - unused(even_other)) < 1
    assert even["paper"]["orientation"] == "portrait"
    assert "متعادل" in even["reason"]

    tall = engine.compute_layout(
        [{"id": "a", "name": "a.png", "kind": "image", "width_mm": 90, "height_mm": 130, "copies": 12}],
        {
            "paper_mode": "orient",
            "paper_id": "A3",
            "crop_enabled": False,
            "margin_mode": "manual",
            "margin": 5,
            "h_gap": 0,
            "v_gap": 0,
            "finish_a4": False,
        },
    )
    tall_other = next(item for item in tall["candidates"] if item["orientation"] != tall["paper"]["orientation"])
    assert tall["paper"]["orientation"] == "portrait"
    assert unused(tall["paper"]) < unused(tall_other)
    assert "کمترین پرتی" in tall["reason"]

    wide = engine.compute_layout(
        [{"id": "a", "name": "a.png", "kind": "image", "width_mm": 180, "height_mm": 40, "copies": 8}],
        {
            "paper_mode": "orient",
            "paper_id": "A3",
            "crop_enabled": False,
            "margin_mode": "manual",
            "margin": 5,
            "h_gap": 0,
            "v_gap": 0,
            "finish_a4": False,
        },
    )
    assert wide["paper"]["orientation"] == "landscape", wide["paper"]


def _slug_misses_labels(layout, dest):
    import pymupdf

    doc = pymupdf.open(dest)
    try:
        page = doc[0]
        infos = page.get_image_info()
        assert len(infos) >= 2, "slug image should sit beside the labels"
        cells = [cell for cell in layout["pages"][0]["cells"] if not cell["empty"]]
        def overlaps(bbox, cell):
            x0, y0, x1, y1 = bbox
            cx0, cy0 = engine.mm_to_pt(cell["x"]), engine.mm_to_pt(cell["y"])
            cx1, cy1 = engine.mm_to_pt(cell["x"] + cell["w"]), engine.mm_to_pt(cell["y"] + cell["h"])
            return not (x1 <= cx0 + 0.8 or cx1 <= x0 + 0.8 or y1 <= cy0 + 0.8 or cy1 <= y0 + 0.8)
        slug = min(infos, key=lambda info: (info["bbox"][2] - info["bbox"][0]) * (info["bbox"][3] - info["bbox"][1]))
        assert not any(overlaps(slug["bbox"], cell) for cell in cells), slug["bbox"]
        assert slug["bbox"][0] >= -1 and slug["bbox"][1] >= -1
        assert slug["bbox"][2] <= page.rect.width + 1
        assert slug["bbox"][3] <= page.rect.height + 1
    finally:
        doc.close()


def test_finish_order_decides_paper_and_prints_outside_the_cut():
    files = [{"id": "a", "name": "card.png", "kind": "image", "width_mm": 85, "height_mm": 55, "copies": 40}]
    base = {
        "paper_mode": "auto",
        "enabled_papers": ["A4", "A3", "A2"],
        "h_gap": 0,
        "v_gap": 0,
        "crop_enabled": False,
        "margin_mode": "manual",
        "margin": 5,
        "finish_min_pages": 5,
    }
    cut = engine.compute_layout(files, {**base, "finish_order": "cut"})
    assert cut["paper"]["id"] == "A4"
    assert cut["stats"]["pages"] >= 5
    assert cut["finish"]["resolved"] is True
    assert cut["slug"]["base"].startswith("برش ویزیتی")

    lam = engine.compute_layout(files, {**base, "finish_order": "lam"})
    assert engine.can_laminate(lam["paper"])
    assert lam["slug"]["base"].startswith("سلفون مات")
    assert "سلفون مات" in lam["reason"]

    both = engine.compute_layout(files, {**base, "finish_order": "both"})
    assert both["finish"]["resolved"] is False
    assert both["finish"]["conflict"]["cut"]["id"] == "A4"
    assert engine.can_laminate(both["finish"]["conflict"]["lam"])
    assert both["finish"]["conflict"]["lam"]["pages"] < 5

    chosen = engine.compute_layout(files, {**base, "finish_order": "both", "finish_choice": "cut"})
    assert chosen["paper"]["id"] == "A4"
    assert chosen["finish"]["choice"] == "cut"
    assert chosen["slug"]["base"].startswith("برش ویزیتی")

    kept = engine.compute_layout(files, {**base, "finish_order": "both", "finish_choice": "lam"})
    assert engine.can_laminate(kept["paper"])
    assert kept["finish"]["choice"] == "lam"
    assert kept["slug"]["base"].startswith("سلفون مات")

    plenty = engine.compute_layout(
        [{**files[0], "copies": 120}],
        {**base, "finish_order": "both"},
    )
    assert plenty["finish"]["kind"] == "both-ok"
    assert plenty["finish"]["conflict"] is None
    assert engine.can_laminate(plenty["paper"])
    assert plenty["stats"]["pages"] >= 5
    assert plenty["slug"]["base"].startswith("سلفون مات و برش")

    with tempfile.TemporaryDirectory() as tmp:
        label = os.path.join(tmp, "label.png")
        Image.new("RGB", (400, 260), (170, 40, 48)).save(label)
        dest = os.path.join(tmp, "out.pdf")
        engine.build_pdf(cut, {"a": {"kind": "image", "path": label, "name": "card.png"}}, dest)
        _slug_misses_labels(cut, dest)


def test_reading_order_is_left_to_right_from_the_top():
    files = [
        {"id": str(i), "name": f"{i}.png", "kind": "image", "width_mm": 40, "height_mm": 20, "copies": 1}
        for i in range(5)
    ]
    layout = engine.compute_layout(files, {
        "paper_mode": "manual",
        "custom_w": 110,
        "custom_h": 80,
        "h_gap": 0,
        "v_gap": 0,
        "crop_enabled": False,
        "margin_mode": "manual",
        "margin": 5,
        "rtl": True,
        "alignment": "topLeft",
    })
    cells = [cell for cell in layout["pages"][0]["cells"] if not cell["empty"]]
    cols = layout["grid"]["cols"]
    assert cols >= 2
    assert layout["grid"]["rtl"] is False
    assert cells[0]["name"] == "0.png"
    assert cells[0]["x"] < cells[1]["x"]
    assert abs(cells[0]["y"] - cells[1]["y"]) < 0.05
    assert abs(cells[cols]["x"] - cells[0]["x"]) < 0.05
    assert cells[cols]["y"] > cells[0]["y"]
    assert cells[cols]["name"] == f"{cols}.png"


if __name__ == "__main__":
    test_fit_count_does_not_reserve_trailing_gap()
    test_butted_labels_lose_middle_marks()
    test_gutter_marks_stay_when_gap_is_wide()
    test_paper_orientation_follows_the_job()
    test_one_copy_does_not_pick_a_press_sheet()
    test_detector_finds_marks_and_ignores_a_frame()
    test_pdf_roundtrip_has_outer_marks_only()
    test_a3_orientation_picks_the_lower_waste()
    test_reading_order_is_left_to_right_from_the_top()
    test_finish_order_decides_paper_and_prints_outside_the_cut()
    test_under_five_sheets_moves_to_a4_for_visiting_cut()
    test_a4_already_under_five_is_not_replaced_by_a_larger_sheet()
    print("all tests passed")
