#property strict
// Read-only tick logger. No orders, no HTTP, no DLL. Run manually in MT5.
input string LoggedSymbol = "XAUUSDm";
input string OutputFile = "Arty_spreads.csv";
int handle = INVALID_HANDLE;
long previous_msc = -1;

int OnInit()
{
   // OnTick belongs to the attached chart: refuse accidental cross-symbol logging.
   if(_Symbol != LoggedSymbol) return INIT_PARAMETERS_INCORRECT;
   handle = FileOpen(OutputFile, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_SHARE_READ, ',');
   if(handle == INVALID_HANDLE) return INIT_FAILED;
   if(FileSize(handle) == 0)
      FileWrite(handle,"server_time_msc","observed_utc","bid","ask","point",
                "spread_usd_per_oz","symbol","broker","account_mode");
   FileSeek(handle,0,SEEK_END);
   return INIT_SUCCEEDED;
}

void OnTick()
{
   MqlTick tick;
   if(!SymbolInfoTick(LoggedSymbol,tick)) return;
   if(tick.time_msc == previous_msc || tick.bid <= 0 || tick.ask < tick.bid) return;
   previous_msc = tick.time_msc;
   // TimeGMT is observation time, NOT a conversion of server tick time.
   // Uses workstation clock: synchronise it before collecting evidence.
   FileWrite(handle,tick.time_msc,TimeToString(TimeGMT(),TIME_DATE|TIME_SECONDS),
             DoubleToString(tick.bid,8),DoubleToString(tick.ask,8),
             DoubleToString(SymbolInfoDouble(LoggedSymbol,SYMBOL_POINT),8),
             DoubleToString(tick.ask-tick.bid,8),LoggedSymbol,
             AccountInfoString(ACCOUNT_SERVER),
             (int)AccountInfoInteger(ACCOUNT_TRADE_MODE));
   FileFlush(handle);
}

void OnDeinit(const int reason)
{
   if(handle != INVALID_HANDLE) FileClose(handle);
}
