#!/usr/bin/env python

import rospy
from moveit_interface import RobotPlanner
from spacial_location import Pose

def save_poses():
    """ Code for saving a specific pose to file
    Intended usage: jog the robot to a position,
    type the name of the pose to save and save it for use in future applications
    """
    rospy.sleep(2)
    robot_planner = RobotPlanner()
    while not rospy.is_shutdown():
        pose_name = raw_input("Type frame name to save, q to exit >  ")
        if pose_name == "q":
            return        
        else:
            cur_pose = robot_planner.current_pose
            print(cur_pose, "cur_pose")
            position = [cur_pose.pose.position.x, cur_pose.pose.position.y, cur_pose.pose.position.z]
            orientation = [cur_pose.pose.orientation.x, cur_pose.pose.orientation.y, cur_pose.pose.orientation.z, cur_pose.pose.orientation.w]
            pose = Pose(position,orientation)
            pose.save(pose_name)
            print("Saved pose: \n{}\n under name {}".format(pose,pose_name))


if __name__ == '__main__':
    rospy.init_node('store_pose',
                        anonymous=True)

    save_poses()
    #sample code for p