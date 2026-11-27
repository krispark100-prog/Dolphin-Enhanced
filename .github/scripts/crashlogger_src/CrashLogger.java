package org.dolphinemu.dolphinemu.utils;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.app.Application;
import android.app.ApplicationExitInfo;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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
  private static final String PREF_THREADS_REPORTED = "threads_reported";
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
      registerEmulationWatcher();
    }
    catch (Throwable ignored)
    {
    }
  }

  /** Cuando se abre la pantalla de emulacion, registra (una vez) como quedaron repartidos los hilos. */
  private static void registerEmulationWatcher()
  {
    if (!(appContext instanceof Application))
      return;
    ((Application) appContext).registerActivityLifecycleCallbacks(
            new Application.ActivityLifecycleCallbacks()
            {
              @Override
              public void onActivityResumed(Activity activity)
              {
                if (!"EmulationActivity".equals(activity.getClass().getSimpleName()))
                  return;
                new Handler(Looper.getMainLooper()).postDelayed(() -> logEmuThreads(), 25000);
              }

              @Override public void onActivityCreated(Activity a, Bundle b) { }
              @Override public void onActivityStarted(Activity a) { }
              @Override public void onActivityPaused(Activity a) { }
              @Override public void onActivityStopped(Activity a) { }
              @Override public void onActivitySaveInstanceState(Activity a, Bundle b) { }
              @Override public void onActivityDestroyed(Activity a) { }
            });
  }

  private static String readSmall(File f)
  {
    try (FileInputStream in = new FileInputStream(f))
    {
      byte[] buf = new byte[2048];
      int n = in.read(buf);
      return n > 0 ? new String(buf, 0, n, "UTF-8") : "";
    }
    catch (Throwable t)
    {
      return "";
    }
  }

  /** Registra nucleos disponibles y en que nucleos/prioridad corre cada hilo importante. */
  public static void logEmuThreads()
  {
    try
    {
      StringBuilder caps = new StringBuilder();
      for (int i = 0; i < Runtime.getRuntime().availableProcessors(); i++)
      {
        String c = readSmall(new File("/sys/devices/system/cpu/cpu" + i + "/cpu_capacity")).trim();
        String fr = readSmall(new File("/sys/devices/system/cpu/cpu" + i +
                "/cpufreq/cpuinfo_max_freq")).trim();
        caps.append(" cpu").append(i).append("=").append(c.isEmpty() ? "?" : c)
                .append("/").append(fr.isEmpty() ? "?" : fr);
      }
      log("HILOS: nucleos (capacidad/freq max kHz):" + caps);

      File[] tasks = new File("/proc/self/task").listFiles();
      if (tasks == null)
        return;
      for (File t : tasks)
      {
        String comm = readSmall(new File(t, "comm")).trim();
        String lower = comm.toLowerCase(Locale.US);
        if (!(lower.contains("cpu") || lower.contains("video") || lower.contains("gpu") ||
                lower.contains("shader") || lower.contains("emu") || lower.contains("dsp") ||
                lower.contains("audio") || lower.contains("dvd")))
          continue;
        String allowed = "?";
        for (String line : readSmall(new File(t, "status")).split("\n"))
        {
          if (line.startsWith("Cpus_allowed_list"))
            allowed = line.substring(line.indexOf(':') + 1).trim();
        }
        String nice = "?";
        String stat = readSmall(new File(t, "stat"));
        int close = stat.lastIndexOf(')');
        if (close > 0)
        {
          String[] f = stat.substring(close + 2).split(" ");
          if (f.length > 16)
            nice = f[16];
        }
        log("HILO " + comm + " (tid " + t.getName() + ") cpus=" + allowed + " nice=" + nice);
      }

      // Una sola vez por instalacion: mostrar este detalle en el proximo inicio.
      SharedPreferences prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
      if (!prefs.getBoolean(PREF_THREADS_REPORTED, false))
      {
        prefs.edit().putBoolean(PREF_THREADS_REPORTED, true).apply();
        log("ALERTA: informe unico de hilos (se muestra una sola vez)");
      }
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
