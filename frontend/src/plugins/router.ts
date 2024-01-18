// Composables
import { createRouter, createWebHistory } from "vue-router";

const routes = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/Login.vue"),
  },
  {
    path: "/",
    name: "home",
    component: () => import("@/views/Home.vue"),
    children: [
      {
        path: "/",
        name: "dashboard",
        component: () => import("@/views/Dashboard/Base.vue"),
      },
      {
        path: "/platform/:platform",
        name: "platform",
        component: () => import("@/views/Gallery/Base.vue"),
      },
      {
        path: "/rom/:rom",
        name: "rom",
        component: () => import("@/views/Details/Base.vue"),
      },
      {
        path: "/library/scan",
        name: "scan",
        component: () => import("@/views/Library/Scan/Base.vue"),
      },
      {
        path: "/settings/control-panel/",
        name: "controlPanel",
        component: () => import("@/views/Settings/ControlPanel/Base.vue"),
        // children: [
        //   {
        //     path: "/settings/control-panel/general",
        //     name: "controlPanelGeneral",
        //     component: () => import("@/views/Settings/ControlPanel/General/Base.vue"),
        //   },
        //   {
        //     path: "/settings/control-panel/config",
        //     name: "controlPanelConfig",
        //     component: () => import("@/views/Settings/ControlPanel/Config/Base.vue"),
        //   },
        //   {
        //     path: "/settings/control-panel/users",
        //     name: "controlPanelUsers",
        //     component: () => import("@/views/Settings/ControlPanel/Users/Base.vue"),
        //   }
        // ]
      },
      {
        path: "/:pathMatch(.*)*",
        name: "noMatch",
        component: () => import("@/views/Dashboard/Base.vue"),
      },
    ],
  },
];

const router = createRouter({
  // @ts-ignore
  history: createWebHistory(process.env.BASE_URL),
  routes,
});

router.afterEach(() => {
  // Scroll to top to avoid annoying behaviour in mobile
  window.scrollTo({ top: 0, left: 0 });
});

export default router;
