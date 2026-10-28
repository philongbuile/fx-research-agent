//+------------------------------------------------------------------+
//| FxraCalendarExport.mq5 - first-party fx-research-agent service   |
//| Exports the MT5 economic calendar (moderate and high importance) |
//| for USD, EUR, GBP, JPY and AUD to Common\Files\fxra_calendar.json |
//| every few minutes. Read-only: it never trades and sends nothing. |
//| D-40. Install: copy to <terminal data>\MQL5\Services, compile in |
//| MetaEditor; Navigator > Services > Add service > Start.          |
//+------------------------------------------------------------------+
#property service
#property copyright "fx-research-agent (first-party, read-only)"
#property version   "1.00"

input int IntervalSeconds = 300;  // export period
input int DaysBack        = 30;   // history kept for tagging past trades
input int DaysAhead       = 7;    // upcoming events

string CURRENCIES[] = {"USD", "EUR", "GBP", "JPY", "AUD"};
const string OUT_FILE = "fxra_calendar.json";
const string TMP_FILE = "fxra_calendar.tmp";

string Esc(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   return s;
  }

// Calendar values are stored as integers scaled by 10^6; LONG_MIN means "no value".
string Num(long v)
  {
   return v == LONG_MIN ? "null" : DoubleToString(v / 1000000.0, 6);
  }

bool Export()
  {
   datetime now_server = TimeTradeServer();
   datetime from = now_server - DaysBack * 86400, to = now_server + DaysAhead * 86400;
   string out = "{\"generated_server\":" + IntegerToString((long)now_server) +
                ",\"generated_gmt\":" + IntegerToString((long)TimeGMT()) + ",\"events\":[";
   bool first = true;
   for(int c = 0; c < ArraySize(CURRENCIES); c++)
     {
      MqlCalendarValue values[];
      if(CalendarValueHistory(values, from, to, NULL, CURRENCIES[c]) <= 0)
         continue;
      for(int i = 0; i < ArraySize(values); i++)
        {
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev) || ev.importance < CALENDAR_IMPORTANCE_MODERATE)
            continue;
         out += (first ? "" : ",") +
                "{\"id\":" + IntegerToString((long)values[i].id) +
                ",\"event_id\":" + IntegerToString((long)values[i].event_id) +
                ",\"time_server\":" + IntegerToString((long)values[i].time) +
                ",\"time_mode\":" + IntegerToString((int)ev.time_mode) +
                ",\"currency\":\"" + CURRENCIES[c] + "\"" +
                ",\"name\":\"" + Esc(ev.name) + "\"" +
                ",\"code\":\"" + Esc(ev.event_code) + "\"" +
                ",\"importance\":" + IntegerToString((int)ev.importance) +
                ",\"actual\":" + Num(values[i].actual_value) +
                ",\"forecast\":" + Num(values[i].forecast_value) +
                ",\"previous\":" + Num(values[i].prev_value) +
                ",\"impact\":" + IntegerToString((int)values[i].impact_type) + "}";
         first = false;
        }
     }
   out += "]}";
   uchar bytes[];
   int n = StringToCharArray(out, bytes, 0, WHOLE_ARRAY, CP_UTF8) - 1;  // drop the trailing NUL
   int h = FileOpen(TMP_FILE, FILE_WRITE | FILE_BIN | FILE_COMMON);
   if(h == INVALID_HANDLE)
      return false;
   FileWriteArray(h, bytes, 0, n);
   FileClose(h);
   return FileMove(TMP_FILE, FILE_COMMON, OUT_FILE, FILE_COMMON | FILE_REWRITE);
  }

void OnStart()
  {
   while(!IsStopped())
     {
      if(!Export())
         Print("FxraCalendarExport: export failed, error ", GetLastError());
      for(int s = 0; s < IntervalSeconds && !IsStopped(); s++)
         Sleep(1000);
     }
  }
