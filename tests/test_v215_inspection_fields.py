"""v2.15: inspection name and city are saved per inspection, returned by the API and printed on the PDF; the
original file name is kept separately; blank or control-character input is cleaned."""

from __future__ import annotations

import io

import pypdfium2 as pdfium

from tests.conftest import upload, wait_done


def test_inspection_name_and_city_round_trip(client, scene):
    aid = upload(client, scene.rgb, scene.labels, inspection_name="  Rizal St. corner\x07 ", city="Imus",
                 barangay="Anabu I")
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["inspection"] == {"name": "Rizal St. corner", "city": "Imus", "barangay": "Anabu I"}
    assert r["image"]["original_filename"] == "pole.jpg"  # the name never replaces the file name
    assert r["pole"]["code"]  # an internal pole code is still generated


def test_blank_fields_are_none(client, scene):
    aid = upload(client, scene.rgb, scene.labels, inspection_name="   ", city="")
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["inspection"]["name"] is None and r["inspection"]["city"] is None


def test_pdf_title_uses_the_inspection_name(client, scene):
    aid = upload(client, scene.rgb, scene.labels, inspection_name="Pole by the school", city="Imus")
    assert wait_done(client, aid)["status"] == "complete"
    pdf = client.get(f"/api/reports/{aid}/pdf")
    assert pdf.status_code == 200
    text = pdfium.PdfDocument(io.BytesIO(pdf.content))[0].get_textpage().get_text_range()
    assert "Pole by the school" in text and "Imus" in text and "pole.jpg" in text
