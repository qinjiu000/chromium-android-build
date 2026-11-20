#define PY_SSIZE_T_CLEAN
#include <Python.h>

static PyObject *pyhello_add(PyObject *self, PyObject *args)
{
    PyObject *a;
    PyObject *b;

    if (!PyArg_UnpackTuple(args, "add", 2, 2, &a, &b)) {
        return NULL;
    }
    if (!PyLong_Check(a) || !PyLong_Check(b)) {
        PyErr_SetString(PyExc_TypeError, "add arguments must be integers");
        return NULL;
    }
    return PyNumber_Add(a, b);
}

static PyObject *pyhello_platform(PyObject *self, PyObject *args)
{
    PyObject *sys_module;
    PyObject *platform;

    if (!PyArg_UnpackTuple(args, "platform", 0, 0)) {
        return NULL;
    }
    sys_module = PyImport_ImportModule("sys");
    if (sys_module == NULL) {
        return NULL;
    }
    platform = PyObject_GetAttrString(sys_module, "platform");
    Py_DECREF(sys_module);
    return platform;
}

static PyMethodDef pyhello_methods[] = {
    {"add", pyhello_add, METH_VARARGS, "Return the sum of two integers."},
    {"platform", pyhello_platform, METH_VARARGS, "Return sys.platform."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef pyhello_module = {
    PyModuleDef_HEAD_INIT,
    "pyhello",
    NULL,
    -1,
    pyhello_methods
};

PyMODINIT_FUNC PyInit_pyhello(void)
{
    return PyModule_Create(&pyhello_module);
}
