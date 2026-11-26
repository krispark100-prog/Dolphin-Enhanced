package org.dolphinemu.dolphinemu.utils;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.app.ApplicationExitInfo;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Environment;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * Registro de diagnostico (agregado por apply_fixes.py).
 *  - Guarda la traza de cualquier crash de Java.
 *  - Guarda "migas de pan" (log()) para saber que estaba haciendo la app.
 *  - En Android 11+ lee el motivo del ultimo cierre (incluye crashes NATIVOS).
 *  - Al abrir la app muestra un informe que se puede copiar o compartir.
 */
public final class CrashLogger
{
  private static final String CRASH_FILE = "crash_log.txt";
  private static final String CRUMB_FILE = "breadcrumbs.txt";
  private static final String ALERT_FLAG = "alert.flag";
  private static final String PREFS = "crash_logger";
  private static final String PREF_LAST_EXIT = "last_exit_ts";
  private static final int MAX_CRUMB_BYTES = 48 * 1024;

  private static Context appContext;

  private CrashLogger()
  {
  }

  public static void install(Context context)
  {
    try
    {
      appContext = context.getApplicationContext();
      final Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
      Thread.setDefaultUncaughtExceptionHandler((thread, throwable) -> {
        try
        {
          StringWriter sw = new StringWriter();
          throwable.printStackTrace(new PrintWriter(sw));
          String text = "CRASH DE JAVA en hilo '" + thread.getName() + "'\n" + sw + "\n";
          writeFile(CRASH_FILE, text, false);
        }
        catch (Throwable ignored)
        {
        }
        if (previous != null)
          previous.uncaughtException(thread, throwable);
      });
      log("--- app iniciada ---");
    }
    catch (Throwable ignored)
    {
    }
  }

  /** Agrega una linea al registro de eventos. Seguro de llamar desde cualquier hilo. */
  public static synchronized void log(String message)
  {
    try
    {
      if (appContext == null)
        return;
      File f = new File(appContext.getFilesDir(), CRUMB_FILE);
      if (f.length() > MAX_CRUMB_BYTES)
        f.delete();
      String line = new SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(new Date()) + " " +
              message + "\n";
      writeFile(CRUMB_FILE, line, true);
      // Eventos que merecen mostrar el informe aunque la app no haya crasheado.
      if (message.startsWith("ALERTA"))
        writeFile(ALERT_FLAG, "1", false);
    }
    catch (Throwable ignored)
    {
    }
  }

  private static void writeFile(String name, String text, boolean append) throws Exception
  {
    File f = new File(appContext.getFilesDir(), name);
    try (FileOutputStream out = new FileOutputStream(f, append))
    {
      out.write(text.getBytes("UTF-8"));
    }
  }

  private static String readFile(String name, int maxBytes)
  {
    try
    {
      File f = new File(appContext.getFilesDir(), name);
      if (!f.exists())
        return "";
      long len = f.length();
      try (FileInputStream in = new FileInputStream(f))
      {
        if (len > maxBytes)
          in.skip(len - maxBytes);
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = in.read(buf)) > 0)
          bos.write(buf, 0, n);
        return bos.toString("UTF-8");
      }
    }
    catch (Throwable t)
    {
      return "";
    }
  }

  private static String header()
  {
    return "Dispositivo: " + Build.MANUFACTURER + " " + Build.MODEL + " | Android " +
            Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")\n";
  }

  /** Texto imprimible de un volcado binario (para tombstones nativos). */
  private static String printableStrings(InputStream in, int maxChars) throws Exception
  {
    StringBuilder out = new StringBuilder();
    StringBuilder run = new StringBuilder();
    byte[] buf = new byte[4096];
    int n;
    while ((n = in.read(buf)) > 0 && out.length() < maxChars)
    {
      for (int i = 0; i < n; i++)
      {
        int c = buf[i] & 0xff;
        if (c >= 32 && c < 127)
        {
          run.append((char) c);
        }
        else
        {
          if (run.length() >= 5)
            out.append(run).append('\n');
          run.setLength(0);
        }
      }
    }
    if (run.length() >= 5)
      out.append(run).append('\n');
    return out.toString();
  }

  private static String nativeExitReports()
  {
    if (Build.VERSION.SDK_INT < 30 || appContext == null)
      return "";
    StringBuilder sb = new StringBuilder();
    try
    {
      ActivityManager am = (ActivityManager) appContext.getSystemService(Context.ACTIVITY_SERVICE);
      SharedPreferences prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
      long lastSeen = prefs.getLong(PREF_LAST_EXIT, 0);
      long newest = lastSeen;
      List<ApplicationExitInfo> list = am.getHistoricalProcessExitReasons(null, 0, 6);
      for (ApplicationExitInfo info : list)
      {
        if (info.getTimestamp() <= lastSeen)
          continue;
        newest = Math.max(newest, info.getTimestamp());
        int r = info.getReason();
        boolean interesting = r == ApplicationExitInfo.REASON_CRASH ||
                r == ApplicationExitInfo.REASON_CRASH_NATIVE ||
                r == ApplicationExitInfo.REASON_ANR ||
                r == ApplicationExitInfo.REASON_SIGNALED ||
                r == ApplicationExitInfo.REASON_INITIALIZATION_FAILURE ||
                r == ApplicationExitInfo.REASON_LOW_MEMORY ||
                r == ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE;
        if (!interesting)
          continue;
        sb.append("CIERRE ANTERIOR: ").append(reasonName(r)).append(" | estado=")
                .append(info.getStatus()).append(" | ")
                .append(new SimpleDateFormat("dd/MM HH:mm:ss", Locale.US)
                        .format(new Date(info.getTimestamp())))
                .append('\n');
        if (info.getDescription() != null)
          sb.append("Descripcion: ").append(info.getDescription()).append('\n');
        if (r == ApplicationExitInfo.REASON_CRASH_NATIVE)
        {
          try (InputStream in = info.getTraceInputStream())
          {
            if (in != null)
              sb.append("--- Traza nativa (texto extraido) ---\n")
                      .append(printableStrings(in, 6000)).append('\n');
          }
          catch (Throwable ignored)
          {
          }
        }
      }
      prefs.edit().putLong(PREF_LAST_EXIT, newest).apply();
    }
    catch (Throwable ignored)
    {
    }
    return sb.toString();
  }

  private static String reasonName(int r)
  {
    switch (r)
    {
    case ApplicationExitInfo.REASON_CRASH: return "CRASH (Java)";
    case ApplicationExitInfo.REASON_CRASH_NATIVE: return "CRASH NATIVO (C++)";
    case ApplicationExitInfo.REASON_ANR: return "ANR (app congelada)";
    case ApplicationExitInfo.REASON_SIGNALED: return "SENAL (kill)";
    case ApplicationExitInfo.REASON_LOW_MEMORY: return "POCA MEMORIA";
    case ApplicationExitInfo.REASON_INITIALIZATION_FAILURE: return "FALLO DE INICIO";
    case ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE: return "USO EXCESIVO DE RECURSOS";
    default: return "motivo " + r;
    }
  }

  /** Muestra (una sola vez) el informe del ultimo cierre anormal, si lo hubo. */
  public static void showPendingReport(final Activity activity)
  {
    try
    {
      if (appContext == null)
        appContext = activity.getApplicationContext();

      String javaCrash = readFile(CRASH_FILE, 20000);
      String exit = nativeExitReports();
      File alertFile = new File(appContext.getFilesDir(), ALERT_FLAG);
      boolean alert = alertFile.exists();
      if (javaCrash.isEmpty() && exit.isEmpty() && !alert)
        return;
      alertFile.delete();

      final String report = header() + "\n" + javaCrash + "\n" + exit +
              "\n--- Ultimos eventos de la app ---\n" + readFile(CRUMB_FILE, 6000);

      new File(appContext.getFilesDir(), CRASH_FILE).delete();
      copyToUserDir(report);

      TextView tv = new TextView(activity);
      tv.setText(report.length() > 9000 ? report.substring(0, 9000) + "\n[...recortado...]" : report);
      tv.setTextIsSelectable(true);
      tv.setTypeface(Typeface.MONOSPACE);
      tv.setTextSize(11);
      int pad = (int) (12 * activity.getResources().getDisplayMetrics().density);
      tv.setPadding(pad, pad, pad, pad);
      ScrollView sv = new ScrollView(activity);
      sv.addView(tv);

      new AlertDialog.Builder(activity)
              .setTitle("Informe del ultimo error")
              .setView(sv)
              .setPositiveButton("Copiar", (d, w) -> {
                ClipboardManager cm =
                        (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
                cm.setPrimaryClip(ClipData.newPlainText("Dolphin error", report));
                Toast.makeText(activity, "Copiado. Pegalo en el chat.", Toast.LENGTH_LONG).show();
              })
              .setNeutralButton("Compartir", (d, w) -> {
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType("text/plain");
                send.putExtra(Intent.EXTRA_TEXT, report);
                activity.startActivity(Intent.createChooser(send, "Compartir informe"));
              })
              .setNegativeButton("Cerrar", null)
              .show();
    }
    catch (Throwable ignored)
    {
    }
  }

  private static void copyToUserDir(String report)
  {
    try
    {
      File dir = new File(Environment.getExternalStorageDirectory(), "dolphin-mmj");
      if (dir.isDirectory() || dir.mkdirs())
      {
        try (FileOutputStream out = new FileOutputStream(new File(dir, "ultimo_error.txt")))
        {
          out.write(report.getBytes("UTF-8"));
        }
      }
    }
    catch (Throwable ignored)
    {
    }
  }
}
