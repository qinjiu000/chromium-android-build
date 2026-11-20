#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static char *join_path(const char *left, const char *right)
{
    size_t length = strlen(left) + strlen(right) + 2;
    char *result = malloc(length);

    if (result != NULL) {
        snprintf(result, length, "%s/%s", left, right);
    }
    return result;
}

static void print_status(PyStatus status)
{
    fprintf(stderr, "%s: %s\n",
            status.func != NULL ? status.func : "Python configuration",
            status.err_msg != NULL ? status.err_msg : "unknown error");
}

static int append_path(PyConfig *config, const char *path)
{
    wchar_t *wide_path = Py_DecodeLocale(path, NULL);
    PyStatus status;

    if (wide_path == NULL) {
        fprintf(stderr, "Unable to decode Python path: %s\n", path);
        return -1;
    }
    status = PyWideStringList_Append(&config->module_search_paths, wide_path);
    PyMem_RawFree(wide_path);
    if (PyStatus_Exception(status)) {
        print_status(status);
        return -1;
    }
    return 0;
}

static void set_optional_pycache(PyConfig *config, const char *base)
{
    char *path = join_path(base, "pycache");
    if (path != NULL && (mkdir(path, 0700) == 0 || errno == EEXIST)) {
        (void) PyConfig_SetBytesString(config, &config->pycache_prefix, path);
    } else {
        const char *fallback = "/data/local/tmp/pyproto-pycache";
        if (mkdir(fallback, 0700) == 0 || errno == EEXIST) {
            (void) PyConfig_SetBytesString(config, &config->pycache_prefix,
                                           fallback);
        }
    }
    free(path);
}

int main(int argc, char **argv)
{
    PyConfig config;
    PyStatus status;
    struct stat info;
    char *stdlib = NULL;
    char *dynload_root = NULL;
    char *dynload = NULL;
    char *script = NULL;
    FILE *file;
    int result = 1;

    if (argc != 3) {
        fprintf(stderr, "Usage: %s <base-dir> <abi>\n", argv[0]);
        return 1;
    }
    stdlib = join_path(argv[1], "stdlib");
    if (stdlib == NULL) {
        fprintf(stderr, "Out of memory\n");
        return 1;
    }
    if (stat(stdlib, &info) != 0 || !S_ISDIR(info.st_mode)) {
        free(stdlib);
        stdlib = join_path(argv[1], "stdlib.zip");
    }
    dynload_root = join_path(argv[1], "lib-dynload");
    script = join_path(argv[1], "run_tests.py");
    if (stdlib == NULL || dynload_root == NULL || script == NULL) {
        fprintf(stderr, "Out of memory\n");
        goto done;
    }
    {
        dynload = join_path(dynload_root, argv[2]);
        if (dynload == NULL) {
            fprintf(stderr, "Out of memory\n");
            goto done;
        }
    }

    file = fopen(script, "r");
    if (file == NULL) {
        perror(script);
        goto done;
    }

    PyConfig_InitIsolatedConfig(&config);
    status = PyConfig_SetBytesString(&config, &config.program_name, argv[0]);
    if (!PyStatus_Exception(status)) {
        status = PyConfig_SetBytesString(&config, &config.base_executable,
                                         argv[0]);
    }
    if (PyStatus_Exception(status)) {
        print_status(status);
        PyConfig_Clear(&config);
        fclose(file);
        goto done;
    }

    set_optional_pycache(&config, argv[1]);
    config.module_search_paths_set = 1;
    if (append_path(&config, stdlib) != 0 ||
        append_path(&config, dynload) != 0 ||
        append_path(&config, argv[1]) != 0) {
        PyConfig_Clear(&config);
        fclose(file);
        goto done;
    }

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        print_status(status);
        fclose(file);
        goto done;
    }

    if (PyRun_SimpleFile(file, script) != 0) {
        if (PyErr_Occurred()) {
            PyErr_Print();
        }
        fclose(file);
        (void) Py_FinalizeEx();
        goto done;
    }
    fclose(file);
    if (Py_FinalizeEx() < 0) {
        fprintf(stderr, "Python finalization failed\n");
        goto done;
    }
    result = 0;

done:
    free(stdlib);
    free(dynload_root);
    free(dynload);
    free(script);
    return result;
}
