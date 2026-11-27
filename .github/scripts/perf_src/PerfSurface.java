package org.dolphinemu.dolphinemu.utils;

import android.app.Activity;
import android.content.res.Configuration;
import android.util.DisplayMetrics;
import android.view.SurfaceHolder;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;

/**
 * Added by apply_fixes.py: renders the final image at a reduced size and lets Android scale it up.
 *
 * At 1x internal resolution the game is only ~640x480, but the swapchain normally has the full screen
 * size (e.g. 1600x720 on a Moto G20). Every frame the GPU then has to clear, blit and present
 * ~1.15M pixels, which is a big share of the work on a weak GPU. With a fixed-size surface the GPU
 * only touches ~0.5M pixels and the display hardware does the upscale for free, with no visible
 * difference at 1x (the image is upscaled with a bilinear filter either way).
 *
 * Only active when the "FixedOutputResolution" option is on (default) and the internal resolution
 * is set to a fixed multiplier (not "auto").
 */
public final class PerfSurface
{
  private PerfSurface()
  {
  }

  public static void apply(Activity activity, SurfaceHolder holder)
  {
    try
    {
      final String gfxIni = DirectoryInitialization.getUserDirectory() + File.separator + "Config" +
              File.separator + "GFX.ini";
      final boolean enabled = readBool(gfxIni, "FixedOutputResolution", true);
      final int internalRes = readInt(gfxIni, "InternalResolution", 1);
      if (!enabled || internalRes <= 0)
        return;  // 0 = "auto" (follows the window size): leave the surface alone

      final DisplayMetrics dm = activity.getResources().getDisplayMetrics();
      final int longSide = Math.max(dm.widthPixels, dm.heightPixels);
      final int shortSide = Math.min(dm.widthPixels, dm.heightPixels);

      // 480 lines per internal-resolution step (native GameCube/Wii output is 480p).
      final int targetShort = 480 * internalRes;
      if (targetShort >= shortSide || shortSide <= 0)
        return;  // already smaller than the target: nothing to save

      int targetLong = Math.round(longSide * (targetShort / (float) shortSide));
      int shortOut = targetShort & ~1;
      int longOut = targetLong & ~1;

      final boolean landscape =
              activity.getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
      if (landscape)
        holder.setFixedSize(longOut, shortOut);
      else
        holder.setFixedSize(shortOut, longOut);

      CrashLogger.log("Superficie fija " + (landscape ? longOut + "x" + shortOut : shortOut + "x" + longOut) +
              " (pantalla " + dm.widthPixels + "x" + dm.heightPixels + ", resolucion interna " +
              internalRes + "x)");
    }
    catch (Throwable t)
    {
      CrashLogger.log("PerfSurface no se aplico: " + t);
    }
  }

  /** Minimal INI reader: value of [Settings] key, or null. */
  private static String readValue(String path, String key)
  {
    File f = new File(path);
    if (!f.isFile())
      return null;
    try (BufferedReader r = new BufferedReader(new FileReader(f)))
    {
      boolean inSettings = false;
      String line;
      while ((line = r.readLine()) != null)
      {
        line = line.trim();
        if (line.startsWith("["))
        {
          inSettings = line.equalsIgnoreCase("[Settings]");
          continue;
        }
        if (!inSettings)
          continue;
        int eq = line.indexOf('=');
        if (eq > 0 && line.substring(0, eq).trim().equalsIgnoreCase(key))
          return line.substring(eq + 1).trim();
      }
    }
    catch (Throwable ignored)
    {
    }
    return null;
  }

  private static boolean readBool(String path, String key, boolean def)
  {
    String v = readValue(path, key);
    return v == null ? def : v.equalsIgnoreCase("true");
  }

  private static int readInt(String path, String key, int def)
  {
    String v = readValue(path, key);
    if (v == null)
      return def;
    try
    {
      return Integer.parseInt(v);
    }
    catch (NumberFormatException e)
    {
      return def;
    }
  }
}
