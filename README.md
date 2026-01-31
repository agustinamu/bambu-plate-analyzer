# Bambu Plate Analyzer

Custom Home Assistant integration that analyzes Bambu Lab pick images and computes bounding boxes for each printable object on the plate.

## Requirements

- [ha-bambulab](https://github.com/greghesp/ha-bambulab) integration installed and configured
- Home Assistant 2024.1+

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Install "Bambu Plate Analyzer"
3. Restart Home Assistant

### Manual

Copy the `custom_components/bambu_plate_analyzer` folder to your Home Assistant `custom_components` directory.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Bambu Plate Analyzer"
3. Enter your Bambu Lab printer serial number

The integration will automatically find the corresponding Bambu Lab entities.

## How it works

1. Listens for changes to the `printable_objects` sensor from ha-bambulab
2. When objects change (new print job), fetches the pick image
3. Scans pixels using the same color→ID algorithm as ha-bambulab
4. Computes bounding boxes (min_x, min_y, max_x, max_y) per object
5. Exposes results as sensor attributes

## Sensor output

The sensor state is the number of detected objects. Attributes:

```json
{
  "image_width": 512,
  "image_height": 512,
  "objects": {
    "123": {"name": "Part1.stl", "bbox": [50, 80, 150, 200]},
    "394": {"name": "Part2.stl", "bbox": [200, 100, 350, 280]}
  }
}
```

The `bbox` format is `[min_x, min_y, max_x, max_y]` in pixels.
