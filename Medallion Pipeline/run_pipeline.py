import time
from bronze import run_bronze
from gold import run_gold
from silver import run_silver


def main():
    start_time = time.time()
    print("=" * 65)
    print("      MEDALLION PIPELINE EXECUTION (SQLITE)")
    print("=" * 65)

    bronze_rows = run_bronze()
    silver_rows = run_silver()
    run_gold()

    elapsed = round(time.time() - start_time, 2)
    print("=" * 65)
    print(f"Pipeline executed successfully in {elapsed}s")
    print(f"Bronze Raw Records   : {bronze_rows}")
    print(f"Silver Clean Records : {silver_rows}")
    print(f"Total Noise/Dropped  : {bronze_rows - silver_rows}")
    print("Storage Target       : data_storage/medallion.db")
    print("=" * 65)


if __name__ == "__main__":
    main()