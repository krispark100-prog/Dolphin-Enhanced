package org.dolphinemu.dolphinemu.ui.main

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Bundle
import android.preference.PreferenceManager
import android.view.Menu
import android.view.MenuItem
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import androidx.recyclerview.widget.DividerItemDecoration
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import org.dolphinemu.dolphinemu.R
import org.dolphinemu.dolphinemu.activities.EmulationActivity.Companion.launch
import org.dolphinemu.dolphinemu.adapters.GameAdapter
import org.dolphinemu.dolphinemu.features.settings.ui.MenuTag
import org.dolphinemu.dolphinemu.features.settings.ui.SettingsActivity
import org.dolphinemu.dolphinemu.model.GameFileCache
import org.dolphinemu.dolphinemu.services.GameFileCacheService
import org.dolphinemu.dolphinemu.utils.ContentHandler
import org.dolphinemu.dolphinemu.utils.DirectoryInitialization
import org.dolphinemu.dolphinemu.utils.FileBrowserHelper
import org.dolphinemu.dolphinemu.utils.Log
import org.dolphinemu.dolphinemu.utils.PermissionsHandler
import org.dolphinemu.dolphinemu.utils.StartupHandler
import org.dolphinemu.dolphinemu.utils.ThemeUtil
import java.io.File

/**
 * The main Activity of the Lollipop style UI. Manages several PlatformGamesFragments, which
 * individually display a grid of available games for each Fragment, in a tabbed layout.
 */
class MainActivity : AppCompatActivity() {
    private var divider: DividerItemDecoration? = null
    private var gameList: RecyclerView? = null
    private var adapter: GameAdapter? = null
    private var toolbar: Toolbar? = null
    private var broadcastReceiver: BroadcastReceiver? = null
    private var dirToAdd: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        ThemeUtil.applyTheme()

        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        findViews()
        setSupportActionBar(toolbar)
        title = String(TITLE_BYTES)

        val filter = IntentFilter()
        filter.addAction(GameFileCacheService.BROADCAST_ACTION)
        broadcastReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                showGames()
            }
        }
        LocalBroadcastManager.getInstance(this).registerReceiver(broadcastReceiver!!, filter)

        // Stuff in this block only happens when this activity is newly created (i.e. not a rotation)
        if (savedInstanceState == null) StartupHandler.HandleInit(this)

        if (PermissionsHandler.hasWriteAccess(this)) {
            showGames()
            GameFileCacheService.startLoad(this)
        }
    }

    override fun onResume() {
        super.onResume()
        dirToAdd?.let { dir ->
            dirToAdd = null
            GameFileCache.addGameFolder(dir, this)
            GameFileCacheService.startRescan(this)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        broadcastReceiver?.let {
            LocalBroadcastManager.getInstance(this).unregisterReceiver(it)
        }
    }

    // TODO: Replace with a ButterKnife injection.
    private fun findViews() {
        val pref = PreferenceManager.getDefaultSharedPreferences(this)
        divider = DividerItemDecoration(this, DividerItemDecoration.VERTICAL)
        toolbar = findViewById(R.id.toolbar_main)
        gameList = findViewById(R.id.grid_games)
        adapter = GameAdapter()
        gameList!!.setAdapter(adapter)
        refreshGameList(pref.getBoolean(PREF_GAMELIST, true))
    }

    private fun refreshGameList(flag: Boolean) {
        val resourceId: Int
        var columns = resources.getInteger(R.integer.game_grid_columns)
        val layoutManager: RecyclerView.LayoutManager
        if (flag) {
            resourceId = R.layout.card_game
            layoutManager = GridLayoutManager(this, columns)
            gameList!!.addItemDecoration(divider!!)
        } else {
            columns = columns * 2 + 1
            resourceId = R.layout.card_game2
            layoutManager = GridLayoutManager(this, columns)
            gameList!!.removeItemDecoration(divider!!)
        }
        adapter!!.setResourceId(resourceId)
        gameList!!.layoutManager = layoutManager
    }

    private fun toggleGameList() {
        val pref = PreferenceManager.getDefaultSharedPreferences(this)
        val flag = !pref.getBoolean(PREF_GAMELIST, true)
        pref.edit().putBoolean(PREF_GAMELIST, flag).apply()
        refreshGameList(flag)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        val inflater = menuInflater
        inflater.inflate(R.menu.menu_game_grid, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        when (item.itemId) {
            R.id.menu_add_directory -> {
                launchFileListActivity()
                return true
            }

            R.id.menu_toggle_list -> {
                toggleGameList()
                return true
            }

            R.id.menu_settings_core -> {
                launchSettingsActivity(MenuTag.CONFIG)
                return true
            }

            R.id.menu_settings_gcpad -> {
                launchSettingsActivity(MenuTag.GCPAD_TYPE)
                return true
            }

            R.id.menu_settings_wiimote -> {
                launchSettingsActivity(MenuTag.WIIMOTE)
                return true
            }

            R.id.menu_clear_data -> {
                clearGameData(this)
                return true
            }

            R.id.menu_refresh -> {
                GameFileCacheService.startRescan(this)
                return true
            }

            R.id.menu_open_file -> {
                launchOpenFileActivity()
                return true
            }
        }

        return false
    }

    private fun launchSettingsActivity(menuTag: MenuTag?) {
        SettingsActivity.launch(this, menuTag, "")
    }

    private fun launchFileListActivity() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        startActivityForResult(intent, REQUEST_ADD_DIRECTORY)
    }

    private fun clearGameData(context: Context) {
        var count = 0
        val cachePath = DirectoryInitialization.getCacheDirectory(context)
        var dir = File(cachePath)
        if (dir.exists()) {
            for (f in dir.listFiles() ?: emptyArray()) {
                if (f.name.endsWith(".uidcache")) {
                    if (f.delete()) {
                        count += 1
                    }
                }
            }
        }

        val shadersPath = cachePath + File.separator + "Shaders"
        dir = File(shadersPath)
        if (dir.exists()) {
            for (f in dir.listFiles() ?: emptyArray()) {
                if (f.name.endsWith(".cache")) {
                    if (f.delete()) {
                        count += 1
                    }
                }
            }
        }

        Toast.makeText(
            context, context.getString(R.string.delete_cache_toast, count),
            Toast.LENGTH_SHORT
        ).show()
    }

    private fun launchOpenFileActivity() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType("*/*")
        startActivityForResult(intent, REQUEST_OPEN_FILE)
    }

    private fun onDirectorySelected(result: Intent) {
        val uri = result.data
        if (uri == null) {
            Log.error("[MainActivity] Directory picker returned no URI")
            return
        }

        // 1) Persist the permission on the exact URI the picker gave us. Doing it before anything
        //    else (and without canonicalize(), which can turn a tree URI into a document URI that
        //    we hold no grant for) avoids the SecurityException that used to crash the app.
        try {
            // We only ever read games, so read access is all we ask to persist.
            val takeFlags = Intent.FLAG_GRANT_READ_URI_PERMISSION
            contentResolver.takePersistableUriPermission(uri, takeFlags)
        } catch (e: SecurityException) {
            Log.warning("[MainActivity] Could not persist permission for $uri: ${e.message}")
            // The grant for this session is still valid, so keep going.
        } catch (e: Exception) {
            Log.error("[MainActivity] takePersistableUriPermission failed: ${e.message}")
            Toast.makeText(this, R.string.write_permission_needed, Toast.LENGTH_LONG).show()
            return
        }

        dirToAdd = uri.toString()

        // 2) Listing a big folder can take a while, so do the sanity check off the UI thread.
        Thread {
            val hasGames = try {
                ContentHandler.getChildNames(uri, false).any { name ->
                    name != null && FileBrowserHelper.GAME_EXTENSIONS.contains(
                        FileBrowserHelper.getExtension(name, false)
                    )
                }
            } catch (e: Exception) {
                true // can't tell; don't nag the user
            }

            if (!hasGames) {
                runOnUiThread {
                    if (isFinishing || isDestroyed) return@runOnUiThread
                    AlertDialog.Builder(this)
                        .setMessage(
                            getString(
                                R.string.wrong_file_extension_in_directory,
                                FileBrowserHelper.setToSortedDelimitedString(
                                    FileBrowserHelper.GAME_EXTENSIONS
                                )
                            )
                        )
                        .setPositiveButton(android.R.string.ok, null)
                        .show()
                }
            }
        }.start()
    }

    /**
     * @param requestCode An int describing whether the Activity that is returning did so successfully.
     * @param resultCode  An int describing what Activity is giving us this callback.
     * @param result      The information the returning Activity is providing us.
     */
    override fun onActivityResult(requestCode: Int, resultCode: Int, result: Intent?) {
        super.onActivityResult(requestCode, resultCode, result)

        // If the user picked a file, as opposed to just backing out.
        if (resultCode == RESULT_OK && result != null) {
            val uri = result.data ?: return
            when (requestCode) {
                REQUEST_ADD_DIRECTORY ->           // If the user picked a file, as opposed to just backing out.
                    onDirectorySelected(result)

                REQUEST_OPEN_FILE ->           // If the user picked a file, as opposed to just backing out.
                    FileBrowserHelper.runAfterExtensionCheck(
                        this, uri,
                        FileBrowserHelper.GAME_LIKE_EXTENSIONS
                    ) { launch(this, uri.toString()) }
            }
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray
    ) {
        when (requestCode) {
            PermissionsHandler.REQUEST_CODE_WRITE_PERMISSION -> if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                DirectoryInitialization.start(this)
                GameFileCacheService.startLoad(this)
            } else {
                Toast.makeText(this, R.string.write_permission_needed, Toast.LENGTH_SHORT)
                    .show()
            }

            else -> super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        }
    }

    fun showGames() {
        adapter!!.swapDataSet(GameFileCacheService.getAllGameFiles())
    }

    companion object {
        const val REQUEST_ADD_DIRECTORY = 1
        const val REQUEST_OPEN_FILE = 2
        const val REQUEST_GPU_DRIVER = 3
        private const val PREF_GAMELIST = "GAME_LIST_TYPE"
        private val TITLE_BYTES = byteArrayOf(
            0x44,
            0x6f,
            0x6c,
            0x70,
            0x68,
            0x69,
            0x6e,
            0x20,
            0x35,
            0x2e,
            0x30,
            0x28,
            0x4d,
            0x4d,
            0x4a,
            0x29
        )
    }
}
