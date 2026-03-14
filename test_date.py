import datetime
import zoneinfo

def format_date(dt_str):
    try:
        dt_str = dt_str.strip()
        if not dt_str.endswith('Z'):
            return dt_str
        dt = datetime.datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        dt_est = dt.astimezone(zoneinfo.ZoneInfo("America/New_York"))
        
        def get_suffix(d):
            return 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')
            
        return dt_est.strftime(f"%a %B %-d{get_suffix(dt_est.day)} %-I:%M %p EST")
    except Exception as e:
        print(f"Error: {e}")
        return dt_str

print(repr(format_date("2026-03-14T00:10:00Z")))
