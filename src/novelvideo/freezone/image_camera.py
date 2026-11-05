"""Catalog of selectable camera settings for still-image generation."""

FREEZONE_IMAGE_CAMERA_OPTIONS = {
    "camera_bodies": [
        {"id": "panavision_dxl2", "label": "Panavision DXL2"},
        {"id": "arri_alexa_65", "label": "ARRI ALEXA 65"},
        {"id": "red_vraptor_xl", "label": "RED V-Raptor XL"},
        {"id": "sony_venice_2", "label": "Sony Venice 2"},
    ],
    "lenses": [
        {"id": "arri_signature_prime", "label": "Arri Signature Prime"},
        {"id": "cooke_s4i", "label": "Cooke S4/i"},
        {"id": "zeiss_supreme_prime", "label": "Zeiss Supreme Prime"},
        {"id": "panavision_primo_70", "label": "Panavision Primo 70"},
    ],
    "focal_lengths_mm": [8, 14, 24, 35, 50, 75, 125],
    "apertures": ["f/1.4", "f/2", "f/2.8", "f/4", "f/5.6", "f/8"],
}
