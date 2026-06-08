import argparse
import re
from pathlib import Path

import pandas as pd


DEFAULT_ENGLAND_WALES = Path("data/input/England and Wales - one postcode per LSOA.xlsx")
DEFAULT_SCOTLAND = Path("data/input/AllDZ_OnePostcodePerDZ_RUC_SeeNotesTab.xlsx")
DEFAULT_OUTPUT = Path("data/input/postcodes_full.csv")


def clean_postcode(postcode):
    return re.sub(r"\s+", "", str(postcode)).lower()


def read_england_wales(path):
    df = pd.read_excel(path, usecols=["POSTCODE"])
    return df["POSTCODE"].dropna().map(clean_postcode)


def read_scotland(path):
    df = pd.read_excel(path, sheet_name="Sheet1", usecols=["Postcode"])
    return df["Postcode"].dropna().map(clean_postcode)


def parse_args():
    parser = argparse.ArgumentParser(description="Export a combined full postcode CSV.")
    parser.add_argument("--england-wales", default=str(DEFAULT_ENGLAND_WALES))
    parser.add_argument("--scotland", default=str(DEFAULT_SCOTLAND))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def main():
    args = parse_args()
    postcodes = pd.concat(
        [
            read_england_wales(Path(args.england_wales)),
            read_scotland(Path(args.scotland)),
        ],
        ignore_index=True,
    )
    postcodes = postcodes.drop_duplicates().sort_values()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    postcodes.to_frame(name="postcode").to_csv(output, index=False, encoding="utf-8-sig")
    print(f"Exported {len(postcodes)} postcodes to {output}")


if __name__ == "__main__":
    main()
