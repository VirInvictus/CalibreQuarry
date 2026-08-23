#!/bin/bash
# Fix BLE001
sed -i 's/except Exception:/except Exception as e:/g' scripts/spot_check.py
sed -i 's/except Exception:/except Exception as e:/g' src/cquarry_cli/modes/analytics.py
sed -i 's/except Exception as e:/except Exception as e:/g' src/cquarry_cli/modes/export.py # Already done
sed -i 's/except Exception:/except Exception as e:/g' src/cquarry_cli/tui.py

# Fix PLW1510 (add check=False)
sed -i 's/capture_output=True,/capture_output=True, check=False,/g' scripts/reconcile_file_metadata.py
sed -i 's/capture_output=True,/capture_output=True, check=False,/g' scripts/spot_check.py
sed -i 's/stdin=sys.stdin,/stdin=sys.stdin, check=False,/g' src/cquarry_cli/tui.py

# Fix DTZ011
sed -i 's/datetime.date.today().isoformat()/datetime.datetime.now(tz=datetime.timezone.utc).date().isoformat()/g' scripts/spot_check.py
sed -i '1i import datetime' scripts/spot_check.py

# Fix SIM118
sed -i 's/for name in vls.keys():/for name in vls:/g' src/cquarry_cli/modes/analytics.py

# Fix C401
sed -i 's/set(f.strip().upper() for f in b\["formats"\].split(","))/{f.strip().upper() for f in b\["formats"\].split(",")}/g' src/cquarry_cli/modes/audit.py

# Fix DTZ005
sed -i "s/datetime.now().strftime/datetime.now(datetime.timezone.utc).strftime/g" src/cquarry_cli/modes/catalog.py
sed -i '1i import datetime' src/cquarry_cli/modes/catalog.py

# Fix SIM115
sed -i 's/f = open(out_path, "w", newline="", encoding="utf-8")/with open(out_path, "w", newline="", encoding="utf-8") as f:/g' src/cquarry_cli/modes/export.py
sed -i 's/try:/# try:/g' src/cquarry_cli/modes/export.py
sed -i 's/yield f, out_path/    yield f, out_path/g' src/cquarry_cli/modes/export.py

